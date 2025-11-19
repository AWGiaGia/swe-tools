import os, json


src = "/home/jiawei/RepoCodeLoc/tools/GetGTTests/gt_tests_scikit-learn.json"
instance_id = "scikit-learn__scikit-learn-25638"


tgt = f"./{instance_id}_gt_tests_scikit-learn.json"


with open(src, "r") as f:
    src_data = json.load(f)

tgt_data = [item for item in src_data if item["instance_id"] == instance_id]
with open(tgt, "w") as f:
    json.dump(tgt_data, f, indent=4)