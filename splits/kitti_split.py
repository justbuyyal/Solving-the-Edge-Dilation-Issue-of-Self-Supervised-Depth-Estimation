import os
import random
'''
def random_sample_files(file_list, num_samples):
    random.shuffle(file_list)  # Shuffle the file list randomly
    sublist_size = len(file_list) // num_samples  # Calculate the size of each sublist

    # Create a list to store the sampled files
    sampled_files = []

    # Iterate and sample the files
    for i in range(num_samples):
        # Get the sublist for the current iteration
        sublist = file_list[i * sublist_size: (i + 1) * sublist_size]

        # Add the sublist to the sampled files
        sampled_files.append(sublist)

    return sampled_files

# Define the paths to the original Eigen Train file and the mini train file
original_file_path = "/home/user/code/splits/eigen_zhou"
split = 'train'

splits = 4 # Split train dataset into 4

# Read the original file into a list of lines
with open(os.path.join(original_file_path, split + "_files.txt"), "r") as f:
    lines = f.readlines()

# Random sample for 4 times
sampled_files = random_sample_files(lines, splits)

# write the mini train and validation txt files
for i in range(splits):
    with open(os.path.join(original_file_path, 'train_split_' + str(i + 1) + '_files.txt'), 'w') as f_mini:
        for line in sampled_files[i]:
            f_mini.write(line)
'''

import os
from random import sample

# Define the paths to the original Eigen Train file and the mini train file
original_file_path = "/home/user/code/splits/eigen_zhou"

# percent_split = [0.3, 0.5]
# splits = ['mini', 'medium']
percent_split = [0.1]
splits = ['tiny']

# Read the original file into a list of lines
with open(os.path.join(original_file_path, "train_files.txt"), "r") as f:
    lines = f.readlines()
    
    for idx, s in enumerate(splits):
        # calculate number of samples for mini train and validation sets
        num_samples = len(lines)
        num_mini_size = int(num_samples * percent_split[idx])
        print(f'split into {num_mini_size} of {s} split')

        # random select from samples
        random_items = sample(population=lines, k=num_mini_size)

        # write the mini train and validation txt files
        with open(os.path.join(original_file_path, f'train_{s}_files.txt'), 'w') as f_mini:
            for i, line in enumerate(random_items):
                f_mini.write(line)
