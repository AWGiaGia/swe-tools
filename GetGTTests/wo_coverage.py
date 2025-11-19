import json, os


src = "/home/jiawei/RepoCodeLoc/tools/GetGTTests/gt_tests_scikit-learn.json"
tgt = "/home/jiawei/RepoCodeLoc/tools/GetGTTests/wo_coverage_gt_tests_scikit-learn.json"

with open(src, "r") as f:
    src_data = json.load(f)


tgt_data = []
for item in src_data:
    ground_truth_locations = item["ground_truth_locations"]
    for i in range(len(ground_truth_locations)):
        ground_truth_locations[i] = ground_truth_locations[i]['location']
    
    tgt_data.append(item)

with open(tgt, "w") as f:
    json.dump(tgt_data, f, indent=4)