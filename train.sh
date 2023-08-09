DATA_PATH=~/datasets/kitti/
EPOCHS=30
BATCH_SIZE=24
WORKERS=10
SEED=63

python -W ignore train.py \
    --data_path $DATA_PATH \
    --model_name LM_AB_Triplet_Masking_30\
    --num_epochs $EPOCHS \
    --batch_size $BATCH_SIZE \
    --num_workers $WORKERS \
    --mypretrain ./pretrained/lite-mono-pretrain.pth \
    --png \
    --random_seed $SEED \
    --lr 0.0001 5e-6 31 0.0001 1e-5 31


# Training Configurations:
    # LR:
    # --lr 0.1 3e-4 30 0.1 3e-4 30 #SGD
    # --lr 0.0001 5e-6 31 1e-4 0.0001 31

    # Adaptive Blur:
    # --disable_auto_blur \
    # --disable_ambiguity_mask \

    # Triplet Loss:
    # --disable_triplet_loss \
    # --disable_hardest_neg \
    # --disable_isolated_triplet