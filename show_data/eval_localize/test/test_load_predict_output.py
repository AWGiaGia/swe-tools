import os, sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json


from utils.util import load_predict_output


if __name__ == '__main__':
    file_level_path = "/home/jiawei/Agentless/results/sklearn-swe-bench-lite-num-2/file_level/loc_outputs.jsonl"
    edit_loc_path = "/home/jiawei/Agentless/results/sklearn-swe-bench-lite-num-2/edit_location_individual/loc_merged_0-0_outputs.jsonl"

    predict_output = load_predict_output(file_level_path = file_level_path, edit_loc_path = edit_loc_path)

    print(json.dumps(predict_output, indent = 4))