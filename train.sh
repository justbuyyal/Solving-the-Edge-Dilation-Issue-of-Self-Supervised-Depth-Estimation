export CUDA_VISIBLE_DEVICES=$1

DATA_PATH=~/datasets/kitti/
EPOCHS=30
BATCH_SIZE=10
WORKERS=10
SEED=63

python -W ignore -m train \
    --data_path $DATA_PATH \
    --model_name LM_Spect_V2_tiny_work_$1 \
    --num_epochs $EPOCHS \
    --batch_size $BATCH_SIZE \
    --mypretrain ./pretrained/lite-mono-pretrain.pth \
    --png \
    --num_workers $WORKERS \
    --random_seed $SEED \
    --lr 1e-5 5e-6 31 1e-4 1e-5 31 \
    --size tiny \
    --disable_auto_blur \
    --disable_ambiguity_mask \
    --disable_triplet_loss \
    --disable_hardest_neg \
    --disable_isolated_triplet

# python -W ignore train.py \
#     --data_path $DATA_PATH \
#     --model_name LM_KITTI_mini \
#     --num_epochs $EPOCHS \
#     --batch_size $BATCH_SIZE \
#     --mypretrain ./pretrained/lite-mono-pretrain.pth \
#     --png \
#     --num_workers $WORKERS \
#     --random_seed $SEED \
#     --lr 1e-5 5e-6 31 1e-4 1e-5 31 \
#     --size mini \

# python -W ignore train.py \
#     --data_path $DATA_PATH \
#     --model_name LM_KITTI_medium \
#     --num_epochs $EPOCHS \
#     --batch_size $BATCH_SIZE \
#     --mypretrain ./pretrained/lite-mono-pretrain.pth \
#     --png \
#     --num_workers $WORKERS \
#     --random_seed $SEED \
#     --lr 1e-5 5e-6 31 1e-4 1e-5 31 \
#     --size medium \

# python -W ignore train.py \
#     --data_path $DATA_PATH \
#     --model_name LM_AB_work_$1 \
#     --num_epochs $EPOCHS \
#     --batch_size $BATCH_SIZE \
#     --mypretrain ./pretrained/lite-mono-pretrain.pth \
#     --png \
#     --num_workers $WORKERS \
#     --random_seed $SEED \
#     --lr 1e-5 5e-6 31 1e-4 1e-5 31 \
#     --disable_triplet_loss \
#     --disable_hardest_neg \
#     --disable_isolated_triplet