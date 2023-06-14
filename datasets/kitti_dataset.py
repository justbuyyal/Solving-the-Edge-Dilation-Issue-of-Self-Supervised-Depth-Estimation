from __future__ import absolute_import, division, print_function

import os
import skimage.transform
import numpy as np
import PIL.Image as pil

from kitti_utils import generate_depth_map
from .mono_dataset import MonoDataset

'''
    Self-Supervised Monocular Depth Estimation: Solving the Edge-Fattening Problem (WACV 2023)
'''
# =====================================
from seg_utils import *
from PIL import Image
from torchvision import transforms
# =====================================


class KITTIDataset(MonoDataset):
    """Superclass for different types of KITTI dataset loaders
    """
    def __init__(self, *args, **kwargs):
        super(KITTIDataset, self).__init__(*args, **kwargs)

        self.K = np.array([[0.58, 0, 0.5, 0],
                           [0, 1.92, 0.5, 0],
                           [0, 0, 1, 0],
                           [0, 0, 0, 1]], dtype=np.float32)

        self.full_res_shape = (1242, 375)
        self.side_map = {"2": 2, "3": 3, "l": 2, "r": 3}

    def check_depth(self):
        line = self.filenames[0].split()
        scene_name = line[0]
        frame_index = int(line[1])

        velo_filename = os.path.join(
            self.data_path,
            scene_name,
            "velodyne_points/data/{:010d}.bin".format(int(frame_index)))

        return os.path.isfile(velo_filename)

    def get_color(self, folder, frame_index, side, do_flip):
        color = self.loader(self.get_image_path(folder, frame_index, side))

        if do_flip:
            color = color.transpose(pil.FLIP_LEFT_RIGHT)

        return color


class KITTIRAWDataset(KITTIDataset):
    """KITTI dataset which loads the original velodyne depth maps for ground truth
    """
    def __init__(self, *args, **kwargs):
        super(KITTIRAWDataset, self).__init__(*args, **kwargs)
        '''
            Self-Supervised Monocular Depth Estimation: Solving the Edge-Fattening Problem (WACV 2023)
        '''
        # =====================================
        if not self.is_train:
            # segmentation is only needed when training or validating.
            return
        self.resize_seg = transforms.Resize((self.height, self.width,),
                                            interpolation=Image.BILINEAR)
        # =====================================

    def get_image_path(self, folder, frame_index, side, seg=False):
        '''ORIGINAL'''
        # f_str = "{:010d}{}".format(frame_index, self.img_ext)
        # image_path = os.path.join(
        #     self.data_path, folder, "image_0{}/data".format(self.side_map[side]), f_str)
        '''
            Self-Supervised Monocular Depth Estimation: Solving the Edge-Fattening Problem (WACV 2023)
        '''
        # =====================================
        f_str = "{:010d}{}".format(frame_index, '.png' if seg else self.img_ext)
        assert side is not None
        if seg:
            image_path = os.path.join(
                self.data_path, folder, "image_0{}".format(self.side_map[side]), f_str)
        else:
            image_path = os.path.join(
                self.data_path, folder, "image_0{}/data".format(self.side_map[side]), f_str)
        # =====================================
        return image_path
    
    '''
        Self-Supervised Monocular Depth Estimation: Solving the Edge-Fattening Problem (WACV 2023)
    '''
    # =====================================
    def get_item_custom(self, inputs, folder, frame_index, side, do_flip):
        if not self.is_train:
            # semantic segmentation is not needed when testing (inferring).
            return
        raw_seg = self.get_seg_map(folder, frame_index, side, do_flip)
        seg = self.resize_seg(raw_seg)
        inputs[('seg', 0, 0)] = torch.tensor(np.array(seg)).float().unsqueeze(0)

    def get_seg_map(self, folder, frame_index, side, do_flip):
        path = self.get_image_path(folder, frame_index, side, True)
        path = path.replace('kitti', 'kitti/segmentation')

        seg = self.loader(path, mode='P')
        seg_copy = np.array(seg.copy())

        for k in np.unique(seg):
            seg_copy[seg_copy == k] = labels[k].trainId
        seg = Image.fromarray(seg_copy, mode='P')

        if do_flip:
            seg = seg.transpose(pil.FLIP_LEFT_RIGHT)
        return seg
    # =====================================

    def get_depth(self, folder, frame_index, side, do_flip):
        calib_path = os.path.join(self.data_path, folder.split("/")[0])

        velo_filename = os.path.join(
            self.data_path,
            folder,
            "velodyne_points/data/{:010d}.bin".format(int(frame_index)))

        depth_gt = generate_depth_map(calib_path, velo_filename, self.side_map[side])
        depth_gt = skimage.transform.resize(
            depth_gt, self.full_res_shape[::-1], order=0, preserve_range=True, mode='constant')

        if do_flip:
            depth_gt = np.fliplr(depth_gt)

        return depth_gt


class KITTIOdomDataset(KITTIDataset):
    """KITTI dataset for odometry training and testing
    """
    def __init__(self, *args, **kwargs):
        super(KITTIOdomDataset, self).__init__(*args, **kwargs)

    def get_image_path(self, folder, frame_index, side):
        f_str = "{:06d}{}".format(frame_index, self.img_ext)
        image_path = os.path.join(
            self.data_path,
            "sequences/{:02d}".format(int(folder)),
            "image_{}".format(self.side_map[side]),
            f_str)
        return image_path


class KITTIDepthDataset(KITTIDataset):
    """KITTI dataset which uses the updated ground truth depth maps
    """
    def __init__(self, *args, **kwargs):
        super(KITTIDepthDataset, self).__init__(*args, **kwargs)

    def get_image_path(self, folder, frame_index, side):
        f_str = "{:010d}{}".format(frame_index, self.img_ext)
        image_path = os.path.join(
            self.data_path,
            folder,
            "image_0{}/data".format(self.side_map[side]),
            f_str)
        return image_path

    def get_depth(self, folder, frame_index, side, do_flip):
        f_str = "{:010d}.png".format(frame_index)
        depth_path = os.path.join(
            self.data_path,
            folder,
            "proj_depth/groundtruth/image_0{}".format(self.side_map[side]),
            f_str)

        depth_gt = pil.open(depth_path)
        depth_gt = depth_gt.resize(self.full_res_shape, pil.NEAREST)
        depth_gt = np.array(depth_gt).astype(np.float32) / 256

        if do_flip:
            depth_gt = np.fliplr(depth_gt)

        return depth_gt
