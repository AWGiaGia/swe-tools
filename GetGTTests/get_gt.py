import os, sys
import os
import json
from datasets import load_dataset
from utils.util import load_ground_truth

from tqdm import tqdm


class DataItemExample:
    [
        {
            "instance_id": "<instance id>",
            "base_commit": "<base_commit>",
            "issue_description": "<issue description>",
            "ground_truth_patch": "<ground truth patch from swe-bench-data Namely oraca patch>", # 标准的修复补丁
            "oraca_test": "<new tests written after solving the target issue>", # 修复该Issue后新增的测试用例，不能作为定位的依据
            "ground_truth_locations":[
                {  "location": "<file::class.method>",
                    "file_level_coverage": ["covered_test1", "covered_test2", "..."], # 哪些测试函数，覆盖了location所在的文件。这是关联性最差的覆盖
                    "module_level_coverage": ["covered_test1", "covered_test2", "..."], # 哪些测试函数，覆盖了location所在的模块（类，或者单独的方法）
                    "entity_level_coverage": ["covered_test1", "covered_test2", "..."] # 哪些测试函数，覆盖了location所在的具体实体（类的具体方法，或者单独的方法），这是关联性最强的覆盖
                },
                {  "location": "<file::function/class name>",
                    "file_level_coverage": ["covered_test1", "covered_test2", "..."],
                    "module_level_coverage": ["covered_test1", "covered_test2", "..."],
                    "entity_level_coverage": ["covered_test1", "covered_test2", "..."]
                }
            ]
        },
    ]


def get_module_level(location):
    if "." in location.split("::")[1]:
        return location.split("::")[0] + "::" + location.split("::")[1].split(".")[0]
    else:
        return location


def process_item(item, local_repo_path, coverage_map, ground_truth):
    instance_id = item['instance_id']
    base_commit = item['base_commit']
    issue_description = item['problem_statement']
    ground_truth_patch = item['patch']
    oraca_test = item['test_patch']
    ground_truth_locations = []


    # 构建coverage_map_dict
    coverage_map_dict_file_level = dict()
    coverage_map_dict_module_level = dict()
    coverage_map_dict_entity_level = dict()

    for test, coverage in coverage_map.items():
        coverage = coverage['covered_functions'] + coverage['covered_classes']
        for location in coverage:

            file_level_location = location.split("::")[0]
            module_level_location = get_module_level(location)
            entity_level_location = location

            if file_level_location not in coverage_map_dict_file_level:
                coverage_map_dict_file_level[file_level_location] = set()
            coverage_map_dict_file_level[file_level_location].add(test)

            if module_level_location not in coverage_map_dict_module_level:
                coverage_map_dict_module_level[module_level_location] = set()
            coverage_map_dict_module_level[module_level_location].add(test)

            if entity_level_location not in coverage_map_dict_entity_level:
                coverage_map_dict_entity_level[entity_level_location] = set()
            coverage_map_dict_entity_level[entity_level_location].add(test)


    # 遍历得到ground truth locations的覆盖测试用例
    for location in ground_truth[1]:
        file_level_coverage = list(coverage_map_dict_file_level.get(location.split("::")[0], set()))
        # print("="*50)
        # print(f"entity: {location}\nmodule: {get_module_level(location)}")
        # print("="*50)
        module_level_coverage = list(coverage_map_dict_module_level.get(get_module_level(location), set()))
        entity_level_coverage = list(coverage_map_dict_entity_level.get(location, set()))


        ground_truth_locations.append({
            "location": location,
            "file_level_coverage": file_level_coverage,
            "module_level_coverage": module_level_coverage,
            "entity_level_coverage": entity_level_coverage
        })
    
    return {
        "instance_id": instance_id,
        "base_commit": base_commit,
        "issue_description": issue_description,
        "ground_truth_patch": ground_truth_patch,
        "oraca_test": oraca_test,
        "ground_truth_locations": ground_truth_locations
    }




def process_datas(swe_bench_data_path, substring, local_repo_path, coverage_table_folder_path, output_path):
    results = []
    ground_truth_map = load_ground_truth(swe_bench_data_path, local_repo_path, substring)

    swe_bench_subdata = load_dataset(swe_bench_data_path)
    for item in tqdm(swe_bench_subdata['test']):
        instance_id = item['instance_id']
        if substring not in instance_id:
            continue

        with open(os.path.join(coverage_table_folder_path, f"{instance_id}.json"), "r") as f:
            coverage_map = json.load(f)

        ground_truth = ground_truth_map[instance_id]

        results.append(process_item(item, local_repo_path, coverage_map, ground_truth))
    
    with open(output_path, "w") as f:
        json.dump(results, f, indent=4)


if __name__ == "__main__":
    swe_bench_data_path = "/home/jiawei/RepoCodeLoc/swe-bench-lite"
    substring = "scikit"
    local_repo_path = "/home/jiawei/CommitInsight/repos/scikit-learn"
    coverage_table_folder_path = "/home/jiawei/RepoCodeLoc/ours/TestBlindLoc/preprocessing/coverage_map/dynamic/dynamic_scikit-learn_repaired"
    output_path = "./gt_tests_scikit-learn.json"

    process_datas(swe_bench_data_path, substring, local_repo_path, coverage_table_folder_path, output_path)