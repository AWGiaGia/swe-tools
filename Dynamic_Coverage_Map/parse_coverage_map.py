
import argparse
import os
import json
from tqdm import tqdm

class SourceFormExample:
    [
        {
        "test-id": "sklearn/feature_selection/tests/test_variance_threshold.py::test_zero_variance",
        "test-func-id": "sklearn/feature_selection/tests/test_variance_threshold.py:13:test_zero_variance",
        "call-relations": [
            {
                "caller": {
                    "filepath": "sklearn/externals/joblib/compressor.py",
                    "lineno": 568,
                    "func_name": "__init__"
                },
                "callee": {
                    "filepath": "sklearn/externals/joblib/compressor.py",
                    "lineno": 100,
                    "func_name": "__init__"
                }
            },
            {
                "caller": {
                    "filepath": "sklearn/externals/joblib/compressor.py",
                    "lineno": 592,
                    "func_name": "__init__"
                },
                "callee": {
                    "filepath": "sklearn/externals/joblib/compressor.py",
                    "lineno": 100,
                    "func_name": "__init__"
                }
            }
                    ]
        }
    ]


class TargetFormExample:
    {
        "sklearn/neighbors/tests/test_approximate.py::test_lsh_forest_deprecation": {
            "covered_functions": [
            "sklearn/linear_model/sag.py::sag_solver",
            "sklearn/linear_model/tests/test_ridge.py::func",
            "sklearn/feature_extraction/text.py::HashingVectorizer.fit",
            "sklearn/base.py::TransformerMixin.fit_transform",
            "sklearn/feature_extraction/tests/test_text.py::func"],
            "covered_classes": []
        }
    }
# 注意，SourceFormExample和TargetFormExample只是形式对照，二者实际内容含义在本例中并不相同


def convert_item(item):
    """转换单条数据：从源格式到目标格式"""
    test_id = item["test-id"]
    functions = set()
    
    for relation in item.get("call-relations", []):
        caller, callee = relation["caller"], relation["callee"]

        if "class_name" in caller and caller["class_name"] and len(caller["class_name"].strip()) > 0 and not (caller['func_name'][0].isupper() and caller['func_name'] == caller['class_name']):
            functions.add(f"{caller['filepath']}::{caller['class_name']}.{caller['func_name']}")
        else:
            functions.add(f"{caller['filepath']}::{caller['func_name']}")

        if "class_name" in callee and callee["class_name"] and len(callee["class_name"].strip()) > 0 and not (callee['func_name'][0].isupper() and callee['func_name'] == callee['class_name']):
            functions.add(f"{callee['filepath']}::{callee['class_name']}.{callee['func_name']}")
        else:
            functions.add(f"{callee['filepath']}::{callee['func_name']}")
    
    return test_id, {"covered_functions": sorted(functions), "covered_classes": []}

# python parse_coverage_map.py --source_folder /home/jiawei/RepoCodeLoc/tools/Dynamic_Coverage_Map/results_scikit-learn_repaired --save_folder dynamic_scikit-learn_repaired --substring scikit
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source_folder", type=str, required=True, help="Path to the input folder ")
    parser.add_argument("--save_folder", type=str, required=True, help="Path to the output folder")
    parser.add_argument("--substring", type=str, required=True, help="Filter substring for project id")

    args = parser.parse_args()

    for sub_folder in tqdm(os.listdir(args.source_folder)):
        if args.substring not in sub_folder: # 只选择指定的项目
            continue

        source_path = os.path.join(args.source_folder, sub_folder,"result", "traces.json")
        with open(source_path, "r") as f:
            source_data = json.load(f)
        
        target_data = {}
        for item in source_data:
            test_id, converted = convert_item(item)
            target_data[test_id] = converted
        
        os.makedirs(args.save_folder, exist_ok=True)
        # 1776_scikit-learn-25747.json
        # scikit-learn__scikit-learn-25747.json
        save_name = sub_folder.split("_")[-1] # scikit-learn-25747
        save_name = save_name.rsplit("-", 1)[0] + "__" + save_name + ".json" # scikit-learn__scikit-learn-25747
        save_path = os.path.join(args.save_folder, save_name)
        with open(save_path, "w") as f:
            json.dump(target_data, f, indent=4)
        
        

