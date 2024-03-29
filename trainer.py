from __future__ import absolute_import, division, print_function


import time
import torch.optim as optim
from torch.utils.data import DataLoader
from tensorboardX import SummaryWriter

import json

from utils import *
from kitti_utils import *
from layers import *

import datasets
import networks
from linear_warmup_cosine_annealing_warm_restarts_weight_decay import ChainedScheduler

import wandb

# torch.backends.cudnn.benchmark = True


def time_sync():
    # PyTorch-accurate time
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    return time.time()


class Trainer:
    def __init__(self, options):
        self.opt = options
        self.log_path = os.path.join(self.opt.log_dir, self.opt.model_name)

        # checking height and width are multiples of 32
        assert self.opt.height % 32 == 0, "'height' must be a multiple of 32"
        assert self.opt.width % 32 == 0, "'width' must be a multiple of 32"

        self.models = {}
        self.models_pose = {}
        self.parameters_to_train = []
        self.parameters_to_train_pose = []

        self.device = torch.device("cpu" if self.opt.no_cuda else "cuda")
        self.profile = self.opt.profile

        self.num_scales = len(self.opt.scales)
        self.frame_ids = len(self.opt.frame_ids)
        self.num_pose_frames = 2 if self.opt.pose_model_input == "pairs" else self.num_input_frames

        assert self.opt.frame_ids[0] == 0, "frame_ids must start with 0"

        self.use_pose_net = not (self.opt.use_stereo and self.opt.frame_ids == [0])

        if self.opt.use_stereo:
            self.opt.frame_ids.append("s")

        self.models["encoder"] = networks.LiteMono(model=self.opt.model,
                                                   drop_path_rate=self.opt.drop_path,
                                                   width=self.opt.width, height=self.opt.height)

        self.models["encoder"].to(self.device)
        self.parameters_to_train += list(self.models["encoder"].parameters())

        self.models["depth"] = networks.DepthDecoder(self.models["encoder"].num_ch_enc,
                                                     self.opt.scales)
        self.models["depth"].to(self.device)
        self.parameters_to_train += list(self.models["depth"].parameters())

        if self.use_pose_net:
            if self.opt.pose_model_type == "separate_resnet":
                self.models_pose["pose_encoder"] = networks.ResnetEncoder(
                    self.opt.num_layers,
                    self.opt.weights_init == "pretrained",
                    num_input_images=self.num_pose_frames)

                self.models_pose["pose_encoder"].to(self.device)
                self.parameters_to_train_pose += list(self.models_pose["pose_encoder"].parameters())

                self.models_pose["pose"] = networks.PoseDecoder(
                    self.models_pose["pose_encoder"].num_ch_enc,
                    num_input_features=1,
                    num_frames_to_predict_for=2)

            elif self.opt.pose_model_type == "shared":
                self.models_pose["pose"] = networks.PoseDecoder(
                    self.models["encoder"].num_ch_enc, self.num_pose_frames)

            elif self.opt.pose_model_type == "posecnn":
                self.models_pose["pose"] = networks.PoseCNN(
                    self.num_input_frames if self.opt.pose_model_input == "all" else 2)

            self.models_pose["pose"].to(self.device)
            self.parameters_to_train_pose += list(self.models_pose["pose"].parameters())

        if self.opt.predictive_mask:
            assert self.opt.disable_automasking, \
                "When using predictive_mask, please disable automasking with --disable_automasking"

            # Our implementation of the predictive masking baseline has the the same architecture
            # as our depth decoder. We predict a separate mask for each source frame.
            self.models["predictive_mask"] = networks.DepthDecoder(
                self.models["encoder"].num_ch_enc, self.opt.scales,
                num_output_channels=(len(self.opt.frame_ids) - 1))
            self.models["predictive_mask"].to(self.device)
            self.parameters_to_train += list(self.models["predictive_mask"].parameters())

        self.model_optimizer = optim.AdamW(self.parameters_to_train, self.opt.lr[0], weight_decay=self.opt.weight_decay)
        if self.use_pose_net:
            self.model_pose_optimizer = optim.AdamW(self.parameters_to_train_pose, self.opt.lr[3], weight_decay=self.opt.weight_decay)

        self.model_lr_scheduler = ChainedScheduler(
                            self.model_optimizer,
                            T_0=int(self.opt.lr[2]),
                            T_mul=1,
                            eta_min=self.opt.lr[1],
                            last_epoch=-1,
                            max_lr=self.opt.lr[0],
                            warmup_steps=0,
                            gamma=0.9
                        )
        self.model_pose_lr_scheduler = ChainedScheduler(
            self.model_pose_optimizer,
            T_0=int(self.opt.lr[5]),
            T_mul=1,
            eta_min=self.opt.lr[4],
            last_epoch=-1,
            max_lr=self.opt.lr[3],
            warmup_steps=0,
            gamma=0.9
        )

        if self.opt.load_weights_folder is not None:
            self.load_model()

        if self.opt.mypretrain is not None:
            self.load_pretrain()

        print("Training model named:\n  ", self.opt.model_name)
        print("Models and tensorboard events files are saved to:\n  ", self.opt.log_dir)
        print("Training is using:\n  ", self.device)

        # data
        datasets_dict = {"kitti": datasets.KITTIRAWDataset,
                         "kitti_odom": datasets.KITTIOdomDataset}
        self.dataset = datasets_dict[self.opt.dataset]

        fpath = os.path.join(os.path.dirname(__file__), "splits", self.opt.split, "{}_files.txt")

        train_filenames = readlines(fpath.format("train")) if self.opt.size == 'full' else readlines(fpath.format("train_" + self.opt.size))
        val_filenames = readlines(fpath.format("val"))
        img_ext = '.png' if self.opt.png else '.jpg'

        num_train_samples = len(train_filenames)
        self.num_total_steps = num_train_samples // self.opt.batch_size * self.opt.num_epochs

        train_dataset = self.dataset(
            self.opt.data_path, train_filenames, self.opt.height, self.opt.width,
            self.opt.frame_ids, 4, is_train=True, img_ext=img_ext)
        self.train_loader = DataLoader(
            train_dataset, self.opt.batch_size, True,
            num_workers=self.opt.num_workers, pin_memory=False, drop_last=True)
        val_dataset = self.dataset(
            self.opt.data_path, val_filenames, self.opt.height, self.opt.width,
            self.opt.frame_ids, 4, is_train=False, img_ext=img_ext)
        self.val_loader = DataLoader(
            val_dataset, self.opt.batch_size, True,
            num_workers=self.opt.num_workers, pin_memory=False, drop_last=True)
        self.val_iter = iter(self.val_loader)

        self.writers = {}
        for mode in ["train", "val"]:
            self.writers[mode] = SummaryWriter(os.path.join(self.log_path, mode))

        if not self.opt.no_ssim:
            self.ssim = SSIM()
            self.ssim.to(self.device)
            
        '''
            Frequency-Aware Self-Supervised Depth Estimation (WACV 2023)
        '''
        # AutoBlur
        # =====================================
        if not self.opt.disable_auto_blur:
            assert self.opt.receptive_field_of_auto_blur % 2 == 1, \
                'receptive_field_of_auto_blur should be an odd number'
            self.auto_blur = networks.AutoBlurModule(
                self.opt.receptive_field_of_auto_blur,
                hf_pixel_thresh=self.opt.hf_pixel_thresh,
                hf_area_percent_thresh=self.opt.hf_area_percent_thresh,
            )
            self.auto_blur.to(self.device)
        # =====================================

        self.backproject_depth = {}
        self.project_3d = {}
        for scale in self.opt.scales:
            h = self.opt.height // (2 ** scale)
            w = self.opt.width // (2 ** scale)

            self.backproject_depth[scale] = BackprojectDepth(self.opt.batch_size, h, w)
            self.backproject_depth[scale].to(self.device)

            self.project_3d[scale] = Project3D(self.opt.batch_size, h, w)
            self.project_3d[scale].to(self.device)

        self.depth_metric_names = [
            "de/abs_rel", "de/sq_rel", "de/rms", "de/log_rms", "da/a1", "da/a2", "da/a3"]

        print("Using split:\n  ", self.opt.split)
        print("There are {:d} training items and {:d} validation items\n".format(
            len(train_dataset), len(val_dataset)))

        self.save_opts()
        
        # MY_FIX: Set wandb
        # =====================================
        self.wandb = wandb.init(project = "Lite-Mono",
                    name = self.opt.model_name)
        # =====================================

    def set_train(self):
        """Convert all models to training mode
        """
        for m in self.models.values():
            m.train()

    def set_eval(self):
        """Convert all models to testing/evaluation mode
        """
        for m in self.models.values():
            m.eval()
    
    '''MY SAVE BEST FUNCTION'''
    # MY_FIX: Comparing Best & Current Evaluation Score, if better, save it
    # =====================================
    def save_best(self, losses):
        priority_order = ['de/abs_rel', 'de/sq_rel', 'de/rms', 'de/log_rms']
        update_flag = False
        for metric in priority_order:
            if losses[metric] < self.best_models[metric]:
                update_flag = True
                break
        if update_flag:
            self.best_models['de/abs_rel'] = losses['de/abs_rel']
            self.best_models['de/sq_rel'] = losses['de/sq_rel']
            self.best_models['de/rms'] = losses['de/rms']
            self.best_models['de/log_rms'] = losses['de/log_rms']
            return True
        return False
    # =====================================

    def train(self):
        """Run the entire training pipeline
        """
        self.epoch = 0
        self.step = 0
        self.start_time = time.time()
        # MY_FIX: Wandb Watch Depth models & Pose models
        # =====================================
        self.wandb.watch(self.models['encoder'])
        self.wandb.watch(self.models_pose['pose_encoder'])
        # MY_FIX: Best metrics initialization
        self.best_models = {
            'de/abs_rel': 1.0,
            'de/sq_rel': 1.0,
            'de/rms': 100.0,
            'de/log_rms': 1.0
        }
        # =====================================
        for self.epoch in range(self.opt.num_epochs):
            self.run_epoch()
            # MY_FIX: Wandb Log Epoch & LR
            # =====================================
            self.wandb.log({
                'Epoch': (self.epoch + 1),
                'Depth_LR': self.model_optimizer.state_dict()['param_groups'][0]['lr'],
                'Pose_LR': self.model_pose_optimizer.state_dict()['param_groups'][0]['lr']
            })
            # =====================================
            '''ORIGINAL'''
            # ORIGINAL
            # if (self.epoch + 1) % self.opt.save_frequency == 0:
            #     self.save_model()

            # MY_FIX: Saving best model if get a better one
            # =====================================
            metrics = self.evaluate()
            if self.save_best(metrics):
                self.wandb.log({
                    'best/abs_rel': metrics['de/abs_rel'],
                    'best/sq_rel': metrics['de/sq_rel'],
                    'best/rms': metrics['de/rms'],
                    'best/log_rms': metrics['de/log_rms']
                })
                self.save_model()
            # =====================================
        # MY_FIX
        # =====================================
        # Save Checkpoint
        self.save_model(checkpoint=True)
        # Log Best Score as one
        error_metrics = ['abs_rel', 'sq_rel', 'rms', 'log_rms']
        for idx, v in enumerate(self.best_models.values()):
            self.wandb.log({
                error_metrics[idx]: v
            })
        # =====================================

    def run_epoch(self):
        """Run a single epoch of training and validation
        """

        print("Training")
        self.set_train()

        self.model_lr_scheduler.step()
        if self.use_pose_net:
            self.model_pose_lr_scheduler.step()

        for batch_idx, inputs in enumerate(self.train_loader):
            self.model_optimizer.zero_grad()
            if self.use_pose_net:
                self.model_pose_optimizer.zero_grad()
                
            before_op_time = time.time()
            
            outputs, losses = self.process_batch(inputs)
            losses["loss"].backward()
            
            self.model_optimizer.step()
            if self.use_pose_net:
                self.model_pose_optimizer.step()
                
            duration = time.time() - before_op_time

            # log less frequently after the first 2000 steps to save time & disk space
            early_phase = batch_idx % self.opt.log_frequency == 0 and self.step < 20000
            late_phase = self.step % 2000 == 0

            if early_phase or late_phase:
                self.log_time(batch_idx, duration, losses["loss"].cpu().data)
                # MY_FIX: Wandb Log Loss
                # =====================================
                self.wandb.log({
                    'Total_loss': losses['loss'],
                    'Step': self.step
                })
                # =====================================

                if "depth_gt" in inputs:
                    self.compute_depth_losses(inputs, outputs, losses)

                self.log("train", inputs, outputs, losses)
                # self.val()
            self.step += 1

    def process_batch(self, inputs):
        """Pass a minibatch through the network and generate images and losses
        """
        for key, ipt in inputs.items():
            inputs[key] = ipt.to(self.device)
            
        '''
            Frequency-Aware Self-Supervised Depth Estimation (WACV 2023)
        '''
        # AutoBlur
        # =====================================
        if not self.opt.disable_auto_blur:
            for scale in self.opt.scales:
                for f_i in self.opt.frame_ids:
                    inputs[('raw_color', f_i, scale)] = inputs[('color', f_i, scale)]
                    inputs[('color', f_i, scale)] = self.auto_blur(
                        inputs[('color', f_i, scale)])
        # =====================================

        if self.opt.pose_model_type == "shared":
            # If we are using a shared encoder for both depth and pose (as advocated
            # in monodepthv1), then all images are fed separately through the depth encoder.
            all_color_aug = torch.cat([inputs[("color_aug", i, 0)] for i in self.opt.frame_ids])
            all_features = self.models["encoder"](all_color_aug)
            all_features = [torch.split(f, self.opt.batch_size) for f in all_features]

            features = {}
            for i, k in enumerate(self.opt.frame_ids):
                features[k] = [f[i] for f in all_features]

            outputs = self.models["depth"](features[0])
        else:
            # Otherwise, we only feed the image with frame_id 0 through the depth encoder
            features = self.models["encoder"](inputs["color_aug", 0, 0])
            
            outputs = self.models["depth"](features)

        if self.opt.predictive_mask:
            outputs["predictive_mask"] = self.models["predictive_mask"](features)

        if self.use_pose_net:
            outputs.update(self.predict_poses(inputs, features))

        self.generate_images_pred(inputs, outputs)
        losses = self.compute_losses(inputs, outputs)
        
        return outputs, losses

    def predict_poses(self, inputs, features):
        """Predict poses between input frames for monocular sequences.
        """
        outputs = {}
        if self.num_pose_frames == 2:
            # In this setting, we compute the pose to each source frame via a
            # separate forward pass through the pose network.

            # select what features the pose network takes as input
            if self.opt.pose_model_type == "shared":
                pose_feats = {f_i: features[f_i] for f_i in self.opt.frame_ids}
            else:
                pose_feats = {f_i: inputs["color_aug", f_i, 0] for f_i in self.opt.frame_ids}
                '''MY Masking'''
                # MASK
                # =====================================
                if not self.opt.disable_mask:
                    b, _, h, w = inputs["color_aug", 0, 0].shape
                    mask = torch.randn(b, 1, h, w) <= self.opt.mask_ratio # mask of opt.mask_ratio %
                    for f_i in self.opt.frame_ids:
                        pose_feats[f_i][mask.expand_as(pose_feats[f_i])]=0
                # =====================================

            for f_i in self.opt.frame_ids[1:]:
                if f_i != "s":
                    # To maintain ordering we always pass frames in temporal order
                    if f_i < 0:
                        pose_inputs = [pose_feats[f_i], pose_feats[0]]
                    else:
                        pose_inputs = [pose_feats[0], pose_feats[f_i]]

                    if self.opt.pose_model_type == "separate_resnet":
                        pose_inputs = [self.models_pose["pose_encoder"](torch.cat(pose_inputs, 1))]
                    elif self.opt.pose_model_type == "posecnn":
                        pose_inputs = torch.cat(pose_inputs, 1)

                    axisangle, translation = self.models_pose["pose"](pose_inputs)
                    outputs[("axisangle", 0, f_i)] = axisangle
                    outputs[("translation", 0, f_i)] = translation

                    # Invert the matrix if the frame id is negative
                    outputs[("cam_T_cam", 0, f_i)] = transformation_from_parameters(
                        axisangle[:, 0], translation[:, 0], invert=(f_i < 0))

        else:
            # Here we input all frames to the pose net (and predict all poses) together
            if self.opt.pose_model_type in ["separate_resnet", "posecnn"]:
                pose_inputs = torch.cat(
                    [inputs[("color_aug", i, 0)] for i in self.opt.frame_ids if i != "s"], 1)

                if self.opt.pose_model_type == "separate_resnet":
                    pose_inputs = [self.models["pose_encoder"](pose_inputs)]

            elif self.opt.pose_model_type == "shared":
                pose_inputs = [features[i] for i in self.opt.frame_ids if i != "s"]

            axisangle, translation = self.models_pose["pose"](pose_inputs)

            for i, f_i in enumerate(self.opt.frame_ids[1:]):
                if f_i != "s":
                    outputs[("axisangle", 0, f_i)] = axisangle
                    outputs[("translation", 0, f_i)] = translation
                    outputs[("cam_T_cam", 0, f_i)] = transformation_from_parameters(
                        axisangle[:, i], translation[:, i])

        return outputs

    def val(self):
        """Validate the model on a single minibatch
        """
        self.set_eval()
        try:
            inputs = self.val_iter.next()
        except StopIteration:
            self.val_iter = iter(self.val_loader)
            inputs = self.val_iter.next()
        with torch.no_grad():
            outputs, losses = self.process_batch(inputs)

            if "depth_gt" in inputs:
                self.compute_depth_losses(inputs, outputs, losses)

            self.log("val", inputs, outputs, losses)
            del inputs, outputs, losses
        
        self.set_train()
    
    '''
        MY Evaluation
    '''
    # MY_FIX: Copy evaluation from evaluate_depth as the same evaluation function.
    # =====================================
    def evaluate(self):
        import cv2
        cv2.setNumThreads(0)  # This speeds up evaluation 5x on our unix systems (OpenCV 3.3.1)
        MIN_DEPTH = 1e-3
        MAX_DEPTH = 80
        self.set_eval()
        img_ext = '.png' if self.opt.png else '.jpg'
        splits_dir = os.path.join(os.path.dirname(__file__), "splits")
        filenames = readlines(os.path.join(splits_dir, self.opt.eval_split, "test_files.txt"))
        dataset = datasets.KITTIRAWDataset(self.opt.data_path, filenames,
                                           self.opt.height, self.opt.width,
                                           [0], 4, is_train=False, img_ext=img_ext)
        # Fix batch-size = 16
        dataloader = DataLoader(dataset, 16, shuffle=False, num_workers=self.opt.num_workers,
                                pin_memory=True, drop_last=False)
        pred_disps = []
        with torch.no_grad():
            for data in dataloader:
                input_color = data[("color", 0, 0)].cuda()
                output = self.models['depth'](self.models['encoder'](input_color))
                pred_disp, _ = disp_to_depth(output[("disp", 0)], self.opt.min_depth, self.opt.max_depth)
                pred_disp = pred_disp.cpu()[:, 0].numpy()
                pred_disps.append(pred_disp)
        pred_disps = np.concatenate(pred_disps)
        # Load GT
        gt_path = os.path.join(splits_dir, self.opt.eval_split, "gt_depths.npz")
        gt_depths = np.load(gt_path, fix_imports=True, encoding='latin1', allow_pickle=True)["data"]
        # Eval
        errors = []
        ratios = []
        for i in range(pred_disps.shape[0]):
            gt_depth = gt_depths[i]
            gt_height, gt_width = gt_depth.shape[:2]

            pred_disp = pred_disps[i]
            pred_disp = cv2.resize(pred_disp, (gt_width, gt_height))
            pred_depth = 1 / pred_disp

            if self.opt.eval_split == "eigen":
                mask = np.logical_and(gt_depth > MIN_DEPTH, gt_depth < MAX_DEPTH)

                crop = np.array([0.40810811 * gt_height, 0.99189189 * gt_height,
                                0.03594771 * gt_width,  0.96405229 * gt_width]).astype(np.int32)
                crop_mask = np.zeros(mask.shape)
                crop_mask[crop[0]:crop[1], crop[2]:crop[3]] = 1
                mask = np.logical_and(mask, crop_mask)

            pred_depth = pred_depth[mask]
            gt_depth = gt_depth[mask]

            pred_depth *= self.opt.pred_depth_scale_factor
            if not self.opt.disable_median_scaling:
                ratio = np.median(gt_depth) / np.median(pred_depth)
                ratios.append(ratio)
                pred_depth *= ratio

            pred_depth[pred_depth < MIN_DEPTH] = MIN_DEPTH
            pred_depth[pred_depth > MAX_DEPTH] = MAX_DEPTH
            # Output: abs_rel, sq_rel, rmse, rmse_log, a1, a2, a3
            errors.append(compute_errors(gt_depth, pred_depth))
        
        mean_errors = np.array(errors).mean(0)
        mean_errors = mean_errors.tolist()
        del errors, ratios
        self.set_train()
        return {
            'de/abs_rel': float(mean_errors[0]),
            'de/sq_rel': float(mean_errors[1]),
            'de/rms': float(mean_errors[2]),
            'de/log_rms': float(mean_errors[3])
        }
    # =====================================

    def generate_images_pred(self, inputs, outputs):
        """Generate the warped (reprojected) color images for a minibatch.
        Generated images are saved into the `outputs` dictionary.
        """
        for scale in self.opt.scales:
            disp = outputs[("disp", scale)]
            if self.opt.v1_multiscale:
                source_scale = scale
            else:
                disp = F.interpolate(
                    disp, [self.opt.height, self.opt.width], mode="bilinear", align_corners=False)
                source_scale = 0

            _, depth = disp_to_depth(disp, self.opt.min_depth, self.opt.max_depth)

            outputs[("depth", 0, scale)] = depth

            for i, frame_id in enumerate(self.opt.frame_ids[1:]):

                if frame_id == "s":
                    T = inputs["stereo_T"]
                else:
                    T = outputs[("cam_T_cam", 0, frame_id)]

                # from the authors of https://arxiv.org/abs/1712.00175
                if self.opt.pose_model_type == "posecnn":

                    axisangle = outputs[("axisangle", 0, frame_id)]
                    translation = outputs[("translation", 0, frame_id)]

                    inv_depth = 1 / depth
                    mean_inv_depth = inv_depth.mean(3, True).mean(2, True)

                    T = transformation_from_parameters(
                        axisangle[:, 0], translation[:, 0] * mean_inv_depth[:, 0], frame_id < 0)

                cam_points = self.backproject_depth[source_scale](
                    depth, inputs[("inv_K", source_scale)])
                pix_coords = self.project_3d[source_scale](
                    cam_points, inputs[("K", source_scale)], T)

                outputs[("sample", frame_id, scale)] = pix_coords

                outputs[("color", frame_id, scale)] = F.grid_sample(
                    inputs[("color", frame_id, source_scale)],
                    outputs[("sample", frame_id, scale)],
                    padding_mode="border", align_corners=True)

                if not self.opt.disable_automasking:
                    outputs[("color_identity", frame_id, scale)] = \
                        inputs[("color", frame_id, source_scale)]

    def compute_reprojection_loss(self, pred, target):
        """Computes reprojection loss between a batch of predicted and target images
        """
        abs_diff = torch.abs(target - pred)
        l1_loss = abs_diff.mean(1, True)

        if self.opt.no_ssim:
            reprojection_loss = l1_loss
        else:
            ssim_loss = self.ssim(pred, target).mean(1, True)
            reprojection_loss = 0.85 * ssim_loss + 0.15 * l1_loss

        return reprojection_loss

    def compute_losses(self, inputs, outputs):
        """Compute the reprojection and smoothness losses for a minibatch
        """

        losses = {}
        total_loss = 0

        for scale in self.opt.scales:
            loss = 0
            reprojection_losses = []

            if self.opt.v1_multiscale:
                source_scale = scale
            else:
                source_scale = 0
            
            disp = outputs[("disp", scale)]
            '''ORIGINAL'''
            # ORIGINAL
            # color = inputs[("color", 0, scale)]
            '''
                Frequency-Aware Self-Supervised Depth Estimation (WACV 2023)
            '''
            # AutoBlur
            # =====================================
            color = inputs[("color", 0, scale)] if self.opt.disable_ambiguity_mask \
                else inputs[('raw_color', 0, scale)]
            # =====================================
            target = inputs[("color", 0, source_scale)]

            for frame_id in self.opt.frame_ids[1:]:
                pred = outputs[("color", frame_id, scale)]
                reprojection_losses.append(self.compute_reprojection_loss(pred, target))

            reprojection_losses = torch.cat(reprojection_losses, 1)

            if not self.opt.disable_automasking:
                identity_reprojection_losses = []
                for frame_id in self.opt.frame_ids[1:]:
                    pred = inputs[("color", frame_id, source_scale)]
                    identity_reprojection_losses.append(
                        self.compute_reprojection_loss(pred, target))

                identity_reprojection_losses = torch.cat(identity_reprojection_losses, 1)

                if self.opt.avg_reprojection:
                    identity_reprojection_loss = identity_reprojection_losses.mean(1, keepdim=True)
                else:
                    # save both images, and do min all at once below
                    identity_reprojection_loss = identity_reprojection_losses

            elif self.opt.predictive_mask:
                # use the predicted mask
                mask = outputs["predictive_mask"]["disp", scale]
                if not self.opt.v1_multiscale:
                    mask = F.interpolate(
                        mask, [self.opt.height, self.opt.width],
                        mode="bilinear", align_corners=False)

                reprojection_losses *= mask

                # add a loss pushing mask to 1 (using nn.BCELoss for stability)
                weighting_loss = 0.2 * nn.BCELoss()(mask, torch.ones(mask.shape).cuda())
                loss += weighting_loss.mean()

            if self.opt.avg_reprojection:
                reprojection_loss = reprojection_losses.mean(1, keepdim=True)
            else:
                reprojection_loss = reprojection_losses
            
            '''
                Frequency-Aware Self-Supervised Depth Estimation (WACV 2023)
            '''
            # AutoBlur
            # =====================================
            if not self.opt.disable_ambiguity_mask:
                ambiguity_mask = self.compute_ambiguity_mask(
                    inputs, outputs, reprojection_loss, scale)
            # =====================================

            if not self.opt.disable_automasking:
                # add random numbers to break ties
                identity_reprojection_loss += torch.randn(
                    identity_reprojection_loss.shape, device=self.device) * 0.00001

                combined = torch.cat((identity_reprojection_loss, reprojection_loss), dim=1)
            else:
                combined = reprojection_loss

            if combined.shape[1] == 1:
                to_optimise = combined
            else:
                to_optimise, idxs = torch.min(combined, dim=1)
            
            '''
                Frequency-Aware Self-Supervised Depth Estimation (WACV 2023)
            '''
            # AutoBlur
            # =====================================
            if not self.opt.disable_ambiguity_mask:
                to_optimise = to_optimise * ambiguity_mask
            # =====================================

            if not self.opt.disable_automasking:
                outputs["identity_selection/{}".format(scale)] = (
                    idxs > identity_reprojection_loss.shape[1] - 1).float()

            loss += to_optimise.mean()

            mean_disp = disp.mean(2, True).mean(3, True)
            norm_disp = disp / (mean_disp + 1e-7)
            smooth_loss = get_smooth_loss(norm_disp, color)

            loss += self.opt.disparity_smoothness * smooth_loss / (2 ** scale)
            
            total_loss += loss
            losses["loss/{}".format(scale)] = loss

        '''
            Self-Supervised Monocular Depth Estimation: Solving the Edge-Fattening Problem (WACV 2023)
        '''
        # TripletLoss
        # =====================================
        if not self.opt.disable_triplet_loss:
            sgt_loss = self.compute_sgt_loss(inputs, outputs)
            losses['sgt_loss'] = sgt_loss
            total_loss = total_loss + sgt_loss * self.opt.sgt
        # =====================================
        '''ORIGINAL'''
        # ORIGINAL
        # else:
        # total_loss /= self.num_scales
        losses["loss"] = total_loss
               
        return losses
    
    '''
        Self-Supervised Monocular Depth Estimation: Solving the Edge-Fattening Problem (WACV 2023)
    '''
    # TripletLoss
    # =====================================
    def compute_sgt_loss(self, inputs, outputs):
        seg_target = inputs[('seg', 0, 0)]
        N, _, H, W = seg_target.shape
        total_loss = 0

        for s, kernel_size in zip(self.opt.sgt_scales, self.opt.sgt_kernel_size):
            # s: [3, 2, 1]
            pad = kernel_size // 2
            h, w = self.opt.height // 2 ** s, self.opt.width // 2 ** s
            seg = F.interpolate(seg_target, size=(h, w), mode='nearest')
            seg_pad = F.pad(seg, pad=[pad] * 4, value=-1)
            patches = seg_pad.unfold(2, kernel_size, 1).unfold(3, kernel_size, 1)
            aggregated_label = patches - seg.unsqueeze(-1).unsqueeze(-1)
            pos_idx = (aggregated_label == 0).float()  # FIXME: misjudge anchor as positive.
            neg_idx = (aggregated_label != 0).float()
            pos_num = pos_idx.sum(dim=(-1, -2))
            neg_num = neg_idx.sum(dim=(-1, -2))

            is_boundary = (pos_num >= kernel_size - 1) & (neg_num >= kernel_size - 1)

            feature = outputs[('d_feature', s)]
            affinity = self.compute_affinity(feature, kernel_size=kernel_size)
            neg_dist = neg_idx * affinity

            if not self.opt.disable_hardest_neg:
                neg_dist[neg_dist == 0] = 1e3
                neg_dist_x, arg_min_x = torch.min(neg_dist, dim=-1)
                neg_dist, arg_min_y = torch.min(neg_dist_x, dim=-1)
                arg_min_x = torch.gather(arg_min_x, -1,
                                         arg_min_y.unsqueeze(-1)).squeeze(-1)
                neg_dist = neg_dist[is_boundary]
            else:
                neg_dist = neg_dist.sum(dim=(-1, -2))[is_boundary] / \
                           neg_num[is_boundary]

            pos_dist = ((pos_idx * affinity).sum(dim=(-1, -2)) / pos_num)[is_boundary]

            zeros = torch.zeros(pos_dist.shape, device=self.device)
            if not self.opt.disable_isolated_triplet:
                loss = pos_dist + torch.max(zeros, self.opt.sgt_isolated_margin - neg_dist)
            else:
                loss = torch.max(zeros,  self.opt.sgt_margin + pos_dist - neg_dist)
            total_loss = total_loss + loss.mean() / (2 ** s)
        return total_loss

    @staticmethod
    def compute_affinity(feature, kernel_size):
        pad = kernel_size // 2
        feature = F.normalize(feature, dim=1)
        unfolded = F.pad(feature, [pad] * 4).unfold(2, kernel_size, 1).unfold(3, kernel_size, 1)
        feature = feature.unsqueeze(-1).unsqueeze(-1)
        similarity = (feature * unfolded).sum(dim=1, keepdim=True)
        affinity = torch.clamp(2 - 2 * similarity, min=1e-9).sqrt()
        return affinity
    # =====================================
    
    '''
        Frequency-Aware Self-Supervised Depth Estimation (WACV 2023)
    '''
    # AutoBlur
    # =====================================
    @staticmethod
    def extract_ambiguity(ipt):
        grad_r = ipt[:, :, :, :-1] - ipt[:, :, :, 1:]
        grad_b = ipt[:, :, :-1, :] - ipt[:, :, 1:, :]

        grad_l = F.pad(grad_r, (1, 0))
        grad_r = F.pad(grad_r, (0, 1))

        grad_t = F.pad(grad_b, (0, 0, 1, 0))
        grad_b = F.pad(grad_b, (0, 0, 0, 1))

        is_u_same_sign = ((grad_l * grad_r) > 0).any(dim=1, keepdim=True)
        is_v_same_sign = ((grad_t * grad_b) > 0).any(dim=1, keepdim=True)
        is_same_sign = torch.logical_or(is_u_same_sign, is_v_same_sign)

        grad_u = (grad_l.abs() + grad_r.abs()).sum(1, keepdim=True) / 2
        grad_v = (grad_t.abs() + grad_b.abs()).sum(1, keepdim=True) / 2
        grad = torch.sqrt(grad_u ** 2 + grad_v ** 2)

        ambiguity = grad * is_same_sign
        return ambiguity
    
    def compute_ambiguity_mask(self, inputs, outputs,
                               reprojection_loss, scale):
        src_scale = scale if self.opt.v1_multiscale else 0
        min_reproj, min_idx = torch.min(reprojection_loss, dim=1)

        target_ambiguity = self.extract_ambiguity(inputs[("color", 0, src_scale)])

        reproj_ambiguities = []
        for f_i in self.opt.frame_ids[1:]:
            src_ambiguity = self.extract_ambiguity(inputs[("color", f_i, src_scale)])

            reproj_ambiguity = F.grid_sample(
                src_ambiguity, outputs[("sample", f_i, scale)],
                padding_mode="border", align_corners=True)
            reproj_ambiguities.append(reproj_ambiguity)

        reproj_ambiguities = torch.cat(reproj_ambiguities, dim=1)
        reproj_ambiguity = torch.gather(reproj_ambiguities, 1, min_idx.unsqueeze(1))

        synthetic_ambiguity, _ = torch.cat(
            [target_ambiguity, reproj_ambiguity], dim=1).max(dim=1)

        if self.opt.ambiguity_by_negative_exponential:
            ambiguity_mask = torch.exp(-self.opt.negative_exponential_coefficient
                                       * synthetic_ambiguity)
        else:
            ambiguity_mask = synthetic_ambiguity < self.opt.ambiguity_thresh
        return ambiguity_mask
    # =====================================

    def compute_depth_losses(self, inputs, outputs, losses):
        """Compute depth metrics, to allow monitoring during training

        This isn't particularly accurate as it averages over the entire batch,
        so is only used to give an indication of validation performance
        """
        depth_pred = outputs[("depth", 0, 0)]
        depth_pred = torch.clamp(F.interpolate(
            depth_pred, [375, 1242], mode="bilinear", align_corners=False), 1e-3, 80)
        depth_pred = depth_pred.detach()

        depth_gt = inputs["depth_gt"]
        mask = depth_gt > 0

        # garg/eigen crop
        crop_mask = torch.zeros_like(mask)
        crop_mask[:, :, 153:371, 44:1197] = 1
        mask = mask * crop_mask

        depth_gt = depth_gt[mask]
        depth_pred = depth_pred[mask]
        depth_pred *= torch.median(depth_gt) / torch.median(depth_pred)

        depth_pred = torch.clamp(depth_pred, min=1e-3, max=80)

        depth_errors = compute_depth_errors(depth_gt, depth_pred)

        for i, metric in enumerate(self.depth_metric_names):
            losses[metric] = np.array(depth_errors[i].cpu())
        
    def log_time(self, batch_idx, duration, loss):
        """Print a logging statement to the terminal
        """
        samples_per_sec = self.opt.batch_size / duration
        time_sofar = time.time() - self.start_time
        training_time_left = (
            self.num_total_steps / self.step - 1.0) * time_sofar if self.step > 0 else 0
        print_string = "epoch {:>3} | lr {:.6f} |lr_p {:.6f} | batch {:>6} | examples/s: {:5.1f}" + \
            " | loss: {:.5f} | time elapsed: {} | time left: {}"
        print(print_string.format(self.epoch, self.model_optimizer.state_dict()['param_groups'][0]['lr'],
                                  self.model_pose_optimizer.state_dict()['param_groups'][0]['lr'],
                                  batch_idx, samples_per_sec, loss,
                                  sec_to_hm_str(time_sofar), sec_to_hm_str(training_time_left)))

    def log(self, mode, inputs, outputs, losses):
        """Write an event to the tensorboard events file
        """
        writer = self.writers[mode]
        # MY_FIX: Wandb Log Metrics for train & val
        # =====================================
        wandb_dict = {}
        for l, v in losses.items():
            writer.add_scalar("{}".format(l), v, self.step)
            if mode == 'train':
                wandb_dict.update({l:v})
            else:
                wandb_dict.update({(l+'_val'):v})
        self.wandb.log(wandb_dict)
        # =====================================

        for j in range(min(4, self.opt.batch_size)):  # write a maxmimum of four images
            for s in self.opt.scales:
                for frame_id in self.opt.frame_ids:
                    writer.add_image(
                        "color_{}_{}/{}".format(frame_id, s, j),
                        inputs[("color", frame_id, s)][j].data, self.step)
                    if s == 0 and frame_id != 0:
                        writer.add_image(
                            "color_pred_{}_{}/{}".format(frame_id, s, j),
                            outputs[("color", frame_id, s)][j].data, self.step)

                writer.add_image(
                    "disp_{}/{}".format(s, j),
                    normalize_image(outputs[("disp", s)][j]), self.step)

                if self.opt.predictive_mask:
                    for f_idx, frame_id in enumerate(self.opt.frame_ids[1:]):
                        writer.add_image(
                            "predictive_mask_{}_{}/{}".format(frame_id, s, j),
                            outputs["predictive_mask"][("disp", s)][j, f_idx][None, ...],
                            self.step)

                elif not self.opt.disable_automasking:
                    writer.add_image(
                        "automask_{}/{}".format(s, j),
                        outputs["identity_selection/{}".format(s)][j][None, ...], self.step)

    def save_opts(self):
        """Save options to disk so we know what we ran this experiment with
        """
        models_dir = os.path.join(self.log_path, "models")
        if not os.path.exists(models_dir):
            os.makedirs(models_dir)
        to_save = self.opt.__dict__.copy()

        with open(os.path.join(models_dir, 'opt.json'), 'w') as f:
            json.dump(to_save, f, indent=2)
    
    def save_model(self, checkpoint=False):
        """Save model weights to disk
        """
        '''ORIGINAL'''
        # ORIGINAL
        # save_folder = os.path.join(self.log_path, "models", "weights_{}".format(self.best_models['epoch']))
        '''MY'''
        # MY_FIX: Save model into best and only save one best model.
        # =====================================
        save_folder = os.path.join(self.log_path, "models", 'best')
        if checkpoint:
            save_folder = save_folder.replace('best', 'checkpoint')
        if not os.path.exists(save_folder):
            os.makedirs(save_folder)
        # =====================================

        for model_name, model in self.models.items():
            save_path = os.path.join(save_folder, "{}.pth".format(model_name))
            to_save = model.state_dict()
            if model_name == 'encoder':
                # save the sizes - these are needed at prediction time
                to_save['height'] = self.opt.height
                to_save['width'] = self.opt.width
                to_save['use_stereo'] = self.opt.use_stereo
                if checkpoint:
                    to_save['epoch'] = self.opt.num_epochs
            torch.save(to_save, save_path)

        for model_name, model in self.models_pose.items():
            save_path = os.path.join(save_folder, "{}.pth".format(model_name))
            to_save = model.state_dict()
            if checkpoint:
                to_save['epoch'] = self.opt.num_epochs
            torch.save(to_save, save_path)

        save_path = os.path.join(save_folder, "{}.pth".format("adam"))
        torch.save(self.model_optimizer.state_dict(), save_path)

        save_path = os.path.join(save_folder, "{}.pth".format("adam_pose"))
        if self.use_pose_net:
            torch.save(self.model_pose_optimizer.state_dict(), save_path)

    def load_pretrain(self):
        self.opt.mypretrain = os.path.expanduser(self.opt.mypretrain)
        path = self.opt.mypretrain
        model_dict = self.models["encoder"].state_dict()
        pretrained_dict = torch.load(path)['model']
        pretrained_dict = {k: v for k, v in pretrained_dict.items() if (k in model_dict and not k.startswith('norm'))}
        model_dict.update(pretrained_dict)
        self.models["encoder"].load_state_dict(model_dict)
        print('mypretrain loaded.')

    def load_model(self):
        """Load model(s) from disk
        """
        self.opt.load_weights_folder = os.path.expanduser(self.opt.load_weights_folder)

        assert os.path.isdir(self.opt.load_weights_folder), \
            "Cannot find folder {}".format(self.opt.load_weights_folder)
        print("loading model from folder {}".format(self.opt.load_weights_folder))

        for n in self.opt.models_to_load:
            print("Loading {} weights...".format(n))
            path = os.path.join(self.opt.load_weights_folder, "{}.pth".format(n))

            if n in ['pose_encoder', 'pose']:
                model_dict = self.models_pose[n].state_dict()
                pretrained_dict = torch.load(path)
                pretrained_dict = {k: v for k, v in pretrained_dict.items() if k in model_dict}
                model_dict.update(pretrained_dict)
                self.models_pose[n].load_state_dict(model_dict)
            else:
                model_dict = self.models[n].state_dict()
                pretrained_dict = torch.load(path)
                pretrained_dict = {k: v for k, v in pretrained_dict.items() if k in model_dict}
                model_dict.update(pretrained_dict)
                self.models[n].load_state_dict(model_dict)

        # loading adam state

        optimizer_load_path = os.path.join(self.opt.load_weights_folder, "adam.pth")
        optimizer_pose_load_path = os.path.join(self.opt.load_weights_folder, "adam_pose.pth")
        if os.path.isfile(optimizer_load_path):
            print("Loading Adam weights")
            optimizer_dict = torch.load(optimizer_load_path)
            optimizer_pose_dict = torch.load(optimizer_pose_load_path)
            self.model_optimizer.load_state_dict(optimizer_dict)
            self.model_pose_optimizer.load_state_dict(optimizer_pose_dict)
        else:
            print("Cannot find Adam weights so Adam is randomly initialized")