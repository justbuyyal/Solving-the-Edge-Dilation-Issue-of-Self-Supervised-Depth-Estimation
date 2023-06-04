python -W ignore train.py \
    --data_path ~/datasets/kitti/ \
    --model_name LM_KITTI_mini \
    --num_epochs 30 \
    --batch_size 32 \
    --mypretrain ./pretrained/lite-mono-pretrain.pth \
    --png \
    --num_workers 4 \
    --random_seed 1504 \
    --lr 1e-5 5e-6 31 1e-4 1e-5 31 \
    --size mini \

python -W ignore train.py \
    --data_path ~/datasets/kitti/ \
    --model_name LM_KITTI_medium \
    --num_epochs 30 \
    --batch_size 32 \
    --mypretrain ./pretrained/lite-mono-pretrain.pth \
    --png \
    --num_workers 4 \
    --random_seed 1504 \
    --lr 1e-5 5e-6 31 1e-4 1e-5 31 \
    --size medium \

python -W ignore train.py \
    --data_path ~/datasets/kitti/ \
    --model_name LM_KITTI \
    --num_epochs 30 \
    --batch_size 32 \
    --mypretrain ./pretrained/lite-mono-pretrain.pth \
    --png \
    --num_workers 4 \
    --random_seed 1504 \
    --lr 1e-5 5e-6 31 1e-4 1e-5 31 \