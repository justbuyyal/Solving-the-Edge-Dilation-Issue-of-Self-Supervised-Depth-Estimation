SESSION_NAME=$1
tmux new-session -d -s $SESSION_NAME
tmux split-window bash
tmux split-window -h bash
tmux select-pane -U
tmux split-window -h bash
# tmux split-window -h bash
# top left
tmux send -t $SESSION_NAME.0 "export CUDA_VISIBLE_DEVICES=0" C-m
tmux send -t $SESSION_NAME.0 "bash train.sh 0" C-m
# top right
tmux send -t $SESSION_NAME.1 "export CUDA_VISIBLE_DEVICES=1" C-m
tmux send -t $SESSION_NAME.1 "bash train.sh 1" C-m
# bottom left
tmux send -t $SESSION_NAME.2 "export CUDA_VISIBLE_DEVICES=2" C-m
tmux send -t $SESSION_NAME.2 "bash train.sh 2" C-m
# bottom right
tmux send -t $SESSION_NAME.3 "export CUDA_VISIBLE_DEVICES=3" C-m
tmux send -t $SESSION_NAME.3 "bash train.sh 3" C-m