import os, json


src = "/home/jiawei/RepoCodeLoc/tools/GetGTTests/gt_tests_scikit-learn.json"
idx = 13 # 只找一个


start_idx = idx
end_idx = idx + 1
tgt = f"./{start_idx}_{end_idx}_gt_tests_scikit-learn.json"

with open(src, "r") as f:
    src_data = json.load(f)

tgt_data = src_data[start_idx:end_idx]
with open(tgt, "w") as f:
    json.dump(tgt_data, f, indent=4)