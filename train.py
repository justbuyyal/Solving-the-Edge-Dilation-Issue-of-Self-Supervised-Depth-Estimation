from __future__ import absolute_import, division, print_function

from options import LiteMonoOptions
from trainer import Trainer

options = LiteMonoOptions()
opts = options.parse()

'''
    MY
'''
import os
import shutil
def copy_code(opts):
    copy_files = ['datasets', 'networks', 'splits']
    py_files = [i for i in os.listdir(os.getcwd()) if i.endswith('.py')]
    copy_files = copy_files + py_files
    dst_path = os.path.join(os.getcwd(), opts.log_dir, opts.model_name, 'code')
    if os.path.exists(dst_path):
        shutil.rmtree(dst_path)
    os.makedirs(dst_path, exist_ok=True)
    # Make Uncertainty Folder
    if opts.uncertainty:
        unc_path = os.path.join(os.getcwd(), opts.log_dir, opts.model_name, 'uncert')
        if os.path.exists(unc_path):
            shutil.rmtree(unc_path)
        os.makedirs(unc_path, exist_ok=True)
    for file in copy_files:
        src_path = os.path.join(os.getcwd(), file)
        if os.path.isdir(src_path):
            # Directory, copy tree
            shutil.copytree(src_path, os.path.join(dst_path, file))
        else:
            # File, copy file
            shutil.copyfile(src_path, os.path.join(dst_path, file))

import torch
import numpy as np
def set_seed(seed):
    if seed is None:
        seed = 1
    print("Random Seed: {}".format(seed))
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.cuda.manual_seed(seed)
    np.random.seed(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
    

if __name__ == "__main__":
    torch.cuda.empty_cache()
    set_seed(opts.random_seed)
    copy_code(opts)
    trainer = Trainer(opts)
    trainer.train()