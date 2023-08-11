<div id="top" align="center">
  
# Solving the Edge-Dilation Issue of Self-Supervised Depth Estimation
**Conference: Under View**
  
  Cheng-Xun Wen*, Kai-Lung Hua

</div>


## Table of Contents
- [Overview](#overview)
- [Results](#results)
  - [KITTI](#kitti) 
  - [Environment](#environment)
- [Data Preparation](#data-preparation)
- [Single Image Test](#single-image-test)
- [Evaluation](#evaluation)
- [Training](#training)
  - [Dependency Installation](#dependency-installation)
  - [Start Training](#start-training)

## Overview
Recently, self-supervised depth estimation has gained increasing attention due to its ability to estimate depth without requiring annotated depth maps. Many researchers have started focusing on its deploy-ability and have proposed lightweight models. However, they often overlook the common challenges associated with self-supervised learning methods. Within the scope of this study, we tackle the issues of object edge dilation and high-frequency region misjudgment while maintproach not only mitigates these problems but also improves accuracy. Experimental results demonstrate that our method stands out among various lightweight models, achieving outstanding accuracy.

### Baseline Model:
Lite-Mono: A Lightweight CNN and Transformer Architecture for Self-Supervised Monocular Depth Estimation [paper link](https://arxiv.org/abs/2211.13202)

## Results
### KITTI
You can follow the baseline paper download the model weight from this [link](https://github.com/noahzn/Lite-Mono)

### Environment
Changing the compose-file volume data path:
```
volumes:
  - /path/to/your/datasets:/home/user/datasets
```
Building environment with docker-compose:
```
docker compose create & docker compose start
```

## Data Preparation
Please refer to [Monodepth2](https://github.com/nianticlabs/monodepth2) to prepare your KITTI data. 


## Single Image Test
    python test_simple.py --load_weights_folder path/to/your/weights/folder --image_path path/to/your/test/image


## Evaluation
    python evaluate_depth.py --load_weights_folder path/to/your/weights/folder --data_path path/to/kitti_data/ --model lite-mono
  or you can call 'eval.bash' as:
  ```
  bash eval.sh
  ```

## Training
#### dependency installation 
    pip install -r requirement.txt
    
#### start training
    python train.py --data_path path/to/your/data --model_name mytrain --num_epochs 30 --batch_size 12 --mypretrain path/to/your/pretrained/weights  --lr 0.0001 5e-6 31 0.0001 1e-5 31
  or you can call 'train.sh' as:
  ```
  bash train.sh
  ```