import json
from unidiff import PatchSet
from datasets import load_dataset
import git
import os,subprocess
import re
from .get_structure import find_code_structure

LANGUAGE_MAP = [
    'py',
    'java',
    'c',
    'cpp',
    'cc', 
    'go',
    'scala',
]


class GetPatch():
    def __init__(self,local_repo_path):
        self.repo = git.Repo(local_repo_path)
        self.local_repo_path = local_repo_path
        self.repo_url = self.repo.remotes.origin.url

        self.repo_url = re.sub(r'https://[^@]+@', 'https://', self.repo_url)
        if self.repo_url.endswith(".git"):
            self.repo_url = self.repo_url[:-4]

        self.file_patterns = [f"*.{ext}" for ext in LANGUAGE_MAP]

    def __call__(self, patch_content, commit_sha, pr_number=None):
        return self.get_patch_info(patch_content, commit_sha, pr_number)

    # region
    # 输入：
    # --- patch_url: 补丁对应的URL
    # --- token: Open Access token
    # 输出：
    # --- patch_info: 包含补丁文件URL及相关hunks信息的字典
    # 发送请求获取补丁信息，并解析返回的内容以提取hunks信息。
    # endregion
    def get_patch_info(self, patch_content, commit_sha, pr_number=None):
        hunks = self.parse_patch(patch_content)
        if hunks is None:
            return None

        # 设置edit_type
        for i in range(len(hunks)):
            if (len(hunks[i]["before"]) > 0) and (len(hunks[i]["after"]) > 0):
                hunks[i]["edit_type"] = 'modify'
            elif len(hunks[i]["before"]) > 0:
                hunks[i]["edit_type"] = 'del'
            elif (len(hunks[i]["after"]) > 0):
                hunks[i]["edit_type"] = 'add'

        patch_info = {
            'hunks': hunks
        }

        return self.add_logic_path(patch_info, commit_sha, pr_number)

    def add_logic_path(self, data_info, commit_sha, pr_number):
        language_suffix_map = {
            "py": "python",
            "java": "java",
            "c": "c",
            "cpp": "cpp",
            "cc": "cpp"
        }

        # 需要获取的文件列表
        target_files = {}
        for hunk in data_info["hunks"]:
            if hunk["filename"] not in target_files:
                target_files[hunk["filename"]] = None
        
        def check_commit_exists(repo, commit_hash):
            try:
                repo.commit(commit_hash)
                return True
            except:
                return False
        
        if not check_commit_exists(self.repo,commit_sha):
            self.repo.git.checkout(f"pr-{pr_number}")
        
        # 获取相应的文件
        for filename in target_files:
            try:
                filecontent = self.repo.git.show(f"{commit_sha}^:{filename}")
                target_files[filename] = filecontent
            except Exception as e:
                if "Cmd('git') failed due to: exit code(128)" in str(e):
                    # print(f"{filename} is a new file that added with {commit_sha}. So skip it")
                    pass
                else:
                    raise e

        # 为每个hunk补充logic_path
        for i in range(len(data_info["hunks"])):

            item = data_info["hunks"][i]

            language_suffix = item["filename"].split('.')[-1]

            if item["filename"] not in target_files or target_files[item["filename"]] is None:
                # print(f"{item['filename']} is a new added file. So skip it")
                continue

            if language_suffix not in language_suffix_map:
                # print(f"No {language_suffix} allowed. So skip it")
                continue

            code = target_files[item["filename"]]
            language = language_suffix_map[language_suffix]

            if item["before_start"] > -1:
                line_index = item["before_start"] 
            else:
                line_index = item["before_nearby_suffix"]

            code = code.encode('utf-8', 'ignore').decode('utf-8')

            try:
                logic_path = find_code_structure(code,line_index,language)
            except RecursionError as e:
                print(f"RecursionError in {item['filename']} ({data_info['url']}): line({line_index})")
                continue


            # try:
            #     logic_path = find_code_structure(code,line_index,language)
            # except Exception as e:
            #     print(f"line_idx: {line_index}")
            #     print(f"lang: {language}")
            #     import json
            #     with open("recursion_dbg.json",'w') as f:
            #         json.dump(data_info,f,indent = 4)
            #     raise e



            item["logic_path"] = logic_path

            data_info["hunks"][i] = item

        return data_info



    # region
    # 输入：
    # --- ori_hunk：github上原始的hunk划分
    # --- filename：hunk对应的文件名
    # --- header：hunk对应的header
    # --- source_timestamp：更改前时间戳
    # --- target_timestamp：更改后时间戳
    # 输出：
    # --- tgt_hunk：所需要的hunk划分
    # endregion
    def spilit_ori_hunk(self, ori_hunk, filename, header, source_timestamp, target_timestamp):
        before_base_start = ori_hunk.source_start
        after_base_start = ori_hunk.target_start
        tgt_hunks = []

        state = 0
        # 0 --> 未读到'-'
        # 1 --> 读到'-'，未读到'+'
        # 2 --> 读到'+'，未读到''
        # 3 --> 读到''，hunk结束

        before_offset = 0
        after_offset = 0
        delete_len = 0
        insert_len = 0

        tmp_prefix = ''
        warm_up = True
        for line in ori_hunk:
            # 初始化状态 0：准备读取新的 hunk 块
            if state == 0 and warm_up:
                tgt_hunk = {
                    'filename': filename,
                    "before_header_start": before_base_start,
                    "after_header_start": after_base_start,
                    'before_start': -1,
                    'after_start': -1,
                    'before_nearby_suffix': -1,
                    'before_len': -1,
                    'after_len': -1,
                    'before': [],
                    'after': [],
                    'prefix': [],
                    'suffix': [],
                    'header': header,
                    "source_timestamp": source_timestamp,
                    "target_timestamp": target_timestamp
                }

                if tmp_prefix != '':
                    tgt_hunk['prefix'].append(tmp_prefix)

            warm_up = False

            line_type = line.line_type  # 行的类型 ('-', '+', ' ') 表示删除、增加或未变动
            line_value = line.value.strip()  # 行的内容

            # 状态 0：判断进入 1 或 2 状态
            if state == 0:
                if line_type == '-':
                    state = 1
                    tgt_hunk['before_start'] = before_base_start + before_offset
                    tgt_hunk['before'].append(line_value)
                    delete_len += 1
                    before_offset += 1
                elif line_type == '+':
                    state = 2
                    tgt_hunk['after_start'] = after_base_start + after_offset
                    tgt_hunk['after'].append(line_value)
                    insert_len += 1
                    after_offset += 1
                elif line_type == ' ':
                    before_offset += 1
                    after_offset += 1
                    tgt_hunk['prefix'].append(line_value)
                    # 一旦tgt_hunks中已经存在了一个tgt_hunk，则当前处理的tgt_hunk的前缀，就是tgt_hunks[-1]的后缀
                    if len(tgt_hunks) > 0:
                        tgt_hunks[-1]['suffix'].append(line_value)

            # 状态 1：读取删除的行，可能后面会有增加的行
            elif state == 1:
                if line_type == '-':
                    tgt_hunk['before'].append(line_value)
                    delete_len += 1
                    before_offset += 1
                elif line_type == '+':
                    state = 2
                    tgt_hunk['after_start'] = after_base_start + after_offset
                    tgt_hunk['after'].append(line_value)
                    insert_len += 1
                    after_offset += 1
                elif line_type == ' ':
                    state = 3
                    before_offset += 1
                    after_offset += 1
                    # print(f"Here before line is: {before_base_start + before_offset}")
                    # print(f"Here after line is: {after_base_start + after_offset}")
                    tgt_hunk['suffix'].append(line_value)

            # 状态 2：读取增加的行
            elif state == 2:
                if line_type == '+':
                    tgt_hunk['after'].append(line_value)
                    insert_len += 1
                    after_offset += 1
                elif line_type == ' ':
                    state = 3
                    before_offset += 1
                    after_offset += 1
                    # print(f"Here before line is: {before_base_start + before_offset}")
                    # print(f"Here after line is: {after_base_start + after_offset}")
                    tgt_hunk['suffix'].append(line_value)

            # 状态 3：hunk 结束，准备将其加入目标 hunk 列表并重置状态
            if state == 3:
                # 完成删除和增加部分的长度计算
                tgt_hunk['before_len'] = delete_len
                tgt_hunk['after_len'] = insert_len


                tgt_hunk['before_nearby_suffix'] = before_base_start + before_offset - 1

                # 重置删除和增加的长度
                delete_len = 0
                insert_len = 0

                # 将当前 hunk 加入结果列表
                tgt_hunks.append(tgt_hunk)

                tmp_prefix = tgt_hunk['suffix'][-1]
                # 重置状态，准备处理下一个 hunk 块

                warm_up = True
                state = 0

        return tgt_hunks

    # region
    # 输入：
    # --- patch_content: 补丁内容字符串
    # 输出：
    # --- hunks: 处理后的hunk列表
    # 解析补丁文件内容，将其拆分为多个hunk并提取相关信息。
    # endregion
    def parse_patch(self, patch_content: str):

        patch_content = re.sub(r'[\u2028\u2029]', '', patch_content) # 移除 Line Separator (U+2028) 和 Paragraph Separator (U+2029)。
        patch_content = re.sub(r'[^\x20-\x7E\t\r\n]', '', patch_content) # 移除 ASCII 范围外的字符（除空格、可见字符、制表符和换行符外的其他字符）

        patch_content = patch_content.replace('\r\n', '\n')  # 将 CRLF 转换为 LF
        patch_content = patch_content.replace('\r', '')  # 移除孤立的 \r（即 ^M）
        # raise ValueError()

        # # ---------------------------DEBUG
        # with open("patch_content_dbg.txt", 'w') as f:
        #     f.write(patch_content)

        # patch_lines = patch_content.splitlines()
        # for i, line in enumerate(patch_lines,start=1):
        #     try:
        #         patch = PatchSet(patch_lines[:i])
        #     except Exception as e:
        #         if "Hunk is shorter than expected" in str(e):
        #             continue
        #         else:
        #             print(f"Error at line {i}: {e}")
        #             print(f"line content: {line}")
        #             raise ValueError()
        # # ---------------------------DEBUG


        patch = PatchSet(patch_content.splitlines())

        hunks = []
        for patched_file in patch:

            # # 只保留指定代码文件
            # if patched_file.path.split('.')[-1] not in LANGUAGE_MAP:
            #     continue
            

            source_timestamp = patched_file.source_timestamp
            target_timestamp = patched_file.target_timestamp

            for ori_hunk in patched_file:
                # 处理hunk_header:
                hunk_header = ori_hunk.section_header

                tgt_hunks = self.spilit_ori_hunk(ori_hunk, patched_file.path, hunk_header, source_timestamp,
                                                 target_timestamp)

                hunks += tgt_hunks

        return hunks



def load_ground_truth(swe_bench_like_dataset_path, local_repo_path, substring=""):
    ground_truth_map = dict()
    get_patch = GetPatch(local_repo_path=local_repo_path)
    swe_bench_like_dataset = load_dataset(swe_bench_like_dataset_path)

    for item in swe_bench_like_dataset['test']:
        instance_id = item['instance_id']
        if substring not in instance_id:
            continue
        patch = item['patch']
        base_commit = item['base_commit']

        patch_info = get_patch(patch_content=patch, commit_sha=base_commit)

        gt_files = set()
        gt_edit_locs = set()

        for hunk in patch_info['hunks']:
            gt_files.add(hunk['filename'])

            if "logic_path" in hunk and hunk["logic_path"] is not None and len(hunk["logic_path"]) > 0:
                logic_path = hunk['filename'] + '::'
                for level in hunk["logic_path"]:
                    logic_path += level['name'] + '.'
                logic_path = logic_path[:-1]
                gt_edit_locs.add(logic_path)
        
        gt_files = list(gt_files)
        gt_edit_locs = list(gt_edit_locs)


        ground_truth_map[instance_id] = [gt_files, gt_edit_locs]

    return ground_truth_map


def convert_found_edit_locs(data):
    """
    将found_edit_locs格式转换为file::class.function格式
    """
    results = []
    
    for file_path, info_list in data.items():
        for info_str in info_list:
            print("="*20 + " info_str " + "="*20)
            print(f"{file_path=}")
            print(info_str)
            print("="*50)
            
            # 找到所有的 function 行（修复正则表达式，匹配完整函数名）
            function_lines = re.findall(r'function:\s*(.+)', info_str)
            class_lines = re.findall(r'class:\s*(\w+)', info_str)
            
            # 如果有 class 行，记录类名
            standalone_class_name = class_lines[0] if class_lines else None
            
            if function_lines:
                # 处理每个 function 行
                for func_line in function_lines:
                    func_line = func_line.strip()
                    
                    if '.' in func_line:
                        # 格式如 LabelEncoder.transform
                        parts = func_line.split('.')
                        if len(parts) >= 2:
                            # 取最后两部分作为类名和方法名
                            class_part = parts[-2]
                            method_part = parts[-1]
                            result = f"{file_path}::{class_part}.{method_part}"
                        else:
                            # 异常情况，直接使用
                            result = f"{file_path}::{func_line}"
                    else:
                        # 只有函数名，没有类名前缀
                        if standalone_class_name:
                            # 如果有独立的 class: 行，组合使用
                            result = f"{file_path}::{standalone_class_name}.{func_line}"
                        else:
                            # 模块级函数
                            result = f"{file_path}::{func_line}"
                    
                    print(f"{result=}")
                    results.append(result)
                    
            elif standalone_class_name:
                # 只有类名，没有函数
                result = f"{file_path}::{standalone_class_name}"
                print(f"{result=}")
                results.append(result)
            # 注意：如果既没有 function 也没有 class，不添加任何结果
            # 这避免了添加裸文件路径的问题
    
    return results

def load_predict_output(edit_loc_path):
    predict_map = dict()

    edit_locs = []

    with open(edit_loc_path, 'r') as f:
        for line in f:
            edit_locs.append(json.loads(line))
    
    for item in edit_locs:
        if item['instance_id'] not in predict_map:
            predict_map[item['instance_id']] = [None, None]
        

        if "found_edit_locs" in item:
            found_edit_locs = convert_found_edit_locs(item['found_edit_locs'])
        elif "suspicious_locations" in item:
            print(f"Warning: 'suspicious_locations' found in {item['instance_id']}. Using it instead of 'found_edit_locs'.")
            found_edit_locs = item['suspicious_locations']
        elif "enhanced_locations" in item:
            print(f"Warning: 'enhanced_locations' found in {item['instance_id']}. Using it instead of 'found_edit_locs'.")
            found_edit_locs = item['enhanced_locations']


        found_edit_files = [loc.split("::")[0] for loc in found_edit_locs]
        # print("="*50)
        # print(f"{found_edit_files=}")
        # print(f"{found_edit_locs=}")
        # print("="*50)
        # 0: file_level_loc
        # 1: class/func_level_loc
        predict_map[item['instance_id']] = [found_edit_files, found_edit_locs]
        

    return predict_map




def evaluate_edit_locations(X, Y):
    """
    评估代码编辑位置推荐性能
    
    Args:
        X: list, 模型推荐的编辑位置 [x1, x2, ..., xn]
        Y: list, 实际的编辑位置 [y1, y2, ..., ym]
    
    Returns:
        dict: 包含多个评估指标的字典
    """
    # 转换为集合以便计算交集

    X_set = set(X)
    Y_set = set(Y)
    
    # 基础统计量
    true_positives = len(X_set & Y_set)  # 正确推荐的位置
    predicted_count = len(X_set)         # 推荐的总数
    actual_count = len(Y_set)            # 实际需要编辑的总数
    
    # 1. 精确率 (Precision) - 推荐的位置中有多少是正确的
    precision = true_positives / predicted_count if predicted_count > 0 else 0
    
    # 2. 召回率 (Recall) - 实际需要编辑的位置中有多少被找到了
    recall = true_positives / actual_count if actual_count > 0 else 1
    
    # 3. F1-score - 精确率和召回率的调和平均
    f1_score = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    
    # 4. F2-score - 更偏重召回率的F-score (β=2)
    beta = 2
    f2_score = (1 + beta**2) * precision * recall / (beta**2 * precision + recall) if (beta**2 * precision + recall) > 0 else 0
    
    # 5. Jaccard相似度 - 交集与并集的比例
    jaccard_similarity = true_positives / len(X_set | Y_set) if len(X_set | Y_set) > 0 else 1
    
    # 6. 过度推荐率 (Over-recommendation Rate) - 额外推荐的比例
    false_positives = predicted_count - true_positives
    over_recommendation_rate = false_positives / actual_count if actual_count > 0 else 0
    
    # 7. 遗漏率 (Miss Rate) - 未找到的实际编辑位置比例
    false_negatives = actual_count - true_positives
    miss_rate = false_negatives / actual_count if actual_count > 0 else 0
    
    # 8. 推荐效率 (Recommendation Efficiency) - 考虑推荐数量的效率指标
    efficiency = true_positives / predicted_count if predicted_count > 0 else 0
    
    return {
        'precision': precision,
        'recall': recall,                    # 主要关注指标
        'f1_score': f1_score,
        'f2_score': f2_score,               # 偏重召回率
        'jaccard_similarity': jaccard_similarity,
        'over_recommendation_rate': over_recommendation_rate,
        'miss_rate': miss_rate,
        'recommendation_efficiency': efficiency,
        'true_positives': true_positives,
        'false_positives': false_positives,
        'false_negatives': false_negatives,
        'predicted_count': predicted_count,
        'actual_count': actual_count
    }




#  batch evaluation
def batch_evaluation(predictions, ground_truths):
    """
    批量评估多个样本 - 处理字典格式数据
    
    Args:
        predictions: dict, 格式为 {issue_id: [[file_level], [func_level]]}
        ground_truths: dict, 格式为 {issue_id: [[file_level], [func_level]]}
    
    Returns:
        results: dict, 每个issue的评估结果
        metrics_summary: dict, 汇总统计指标
    """
    import numpy as np
    
    results = {}
    
    # 找到共同的issue IDs
    common_issues = set(predictions.keys()) & set(ground_truths.keys())
    
    if not common_issues:
        print("Warning: No common issues found between predictions and ground_truths")
        return {}, {}
    
    print(f"Evaluating {len(common_issues)} common issues...")
    
    for issue_id in common_issues:
        pred_data = predictions[issue_id]
        gt_data = ground_truths[issue_id]
        
        # 确保数据格式正确
        if len(pred_data) != 2 or len(gt_data) != 2:
            print(f"Warning: Skipping {issue_id} due to incorrect data format")
            continue
            
        pred_files, pred_funcs = pred_data[0], pred_data[1]
        gt_files, gt_funcs = gt_data[0], gt_data[1]
        
        # 分别评估文件级和函数级，然后合并评估
        results[issue_id] = {
            'file_level': evaluate_edit_locations(pred_files, gt_files),
            'function_level': evaluate_edit_locations(pred_funcs, gt_funcs),
            'combined': evaluate_edit_locations(pred_files + pred_funcs, gt_files + gt_funcs)
        }
    
    # 计算汇总统计
    metrics_summary = calculate_summary_metrics(results)
    
    return results, metrics_summary

def calculate_summary_metrics(results):
    """
    计算汇总统计指标
    """
    import numpy as np
    
    summary = dict()
    
    # 为每个评估级别计算统计
    for level in ['file_level', 'function_level', 'combined']:
        level_results = [result[level] for result in results.values()]
        
        if not level_results:
            continue
            
        level_summary = {}
        
        # 计算每个指标的统计量
        for metric in level_results[0].keys():
            if isinstance(level_results[0][metric], (int, float)):
                values = [r[metric] for r in level_results]
                level_summary[f'avg_{metric}'] = float(np.mean(values))
                level_summary[f'std_{metric}'] = float(np.std(values))
                level_summary[f'median_{metric}'] = float(np.median(values))
                level_summary[f'min_{metric}'] = float(np.min(values))
                level_summary[f'max_{metric}'] = float(np.max(values))
        
        summary[level] = level_summary
    
    # 添加整体统计信息
    summary['overall_stats'] = {
        'total_issues_evaluated': len(results),
        'issues_with_perfect_recall': sum(1 for r in results.values() 
                                        if r['combined']['recall'] == 1.0),
        'issues_with_zero_recall': sum(1 for r in results.values() 
                                     if r['combined']['recall'] == 0.0),
        'avg_predicted_locations_per_issue': float(np.mean([r['combined']['predicted_count'] 
                                                    for r in results.values()])),
        'avg_actual_locations_per_issue': float(np.mean([r['combined']['actual_count'] 
                                                 for r in results.values()]))
    }
    
    return summary
