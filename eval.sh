MODEL_NAME="LM_KITTI"

python -W ignore evaluate_depth.py \
        --load_weights_folder /home/user/code/tmp/$MODEL_NAME/models/best/ \
        --data_path ~/datasets/kitti \
        --model lite-mono \
        --png \
        --save_pred