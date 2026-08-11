import argparse
import os
import json
from tqdm import tqdm
from collections import defaultdict, deque

class SourceFormExample:
    """
    输入格式示例：
    [
        {
            "test-id": "sklearn/tests/test_isotonic.py::test_isotonic_regression_ties_min",
            "test-func-id": "sklearn/tests/test_isotonic.py:113:test_isotonic_regression_ties_min",
            "call-relations": [
                {
                    "caller": {
                        "filepath": "sklearn/externals/six.py",
                        "lineno": 110,
                        "func_name": "__init__",
                        "class_name": "MovedAttribute"
                    },
                    "callee": {
                        "filepath": "sklearn/externals/six.py",
                        "lineno": 82,
                        "func_name": "__init__",
                        "class_name": "_LazyDescr"
                    }
                }
            ]
        }
    ]
    """
    pass


def format_function_identifier(func_info):
    """
    将函数信息格式化为标准标识符
    格式：filepath::ClassName.method_name 或 filepath::function_name
    
    规则：
    - 如果有类名且函数名不是构造函数（函数名首字母大写且与类名相同），则使用 ClassName.method_name
    - 否则只使用 function_name
    """
    filepath = func_info["filepath"]
    func_name = func_info["func_name"]
    class_name = func_info.get("class_name", "")
    
    # 如果有类名且不为空，并且不是构造函数的特殊情况
    if class_name and class_name.strip():
        # 检查是否是构造函数（函数名首字母大写且与类名相同）
        is_constructor = func_name[0].isupper() and func_name == class_name
        if not is_constructor:
            return f"{filepath}::{class_name}.{func_name}"
    
    return f"{filepath}::{func_name}"


def sort_call_relations_by_depth(test_id, call_relations):
    """
    按照调用深度对调用关系进行排序
    使用BFS从测试函数开始，逐层展开
    
    参数：
    - test_id: 测试用例ID，用于识别起始节点
    - call_relations: 调用关系列表 [(caller, callee), ...]
    
    返回：
    - 按深度排序的调用关系列表
    """
    # 构建邻接表
    graph = defaultdict(list)
    all_edges = []
    
    for caller, callee in call_relations:
        graph[caller].append(callee)
        all_edges.append((caller, callee))
    
    # 找到测试函数节点（起始节点）
    # 测试ID格式如: sklearn/tests/test_isotonic.py::test_isotonic_regression_ties_min
    test_func = test_id
    
    # BFS遍历，记录每个节点的深度
    node_depth = {test_func: 0}
    queue = deque([test_func])
    
    while queue:
        current = queue.popleft()
        current_depth = node_depth[current]
        
        for neighbor in graph[current]:
            if neighbor not in node_depth:
                node_depth[neighbor] = current_depth + 1
                queue.append(neighbor)
    
    # 按照调用者的深度对边进行排序
    def edge_sort_key(edge):
        caller, callee = edge
        caller_depth = node_depth.get(caller, float('inf'))
        callee_depth = node_depth.get(callee, float('inf'))
        # 首先按调用者深度排序，然后按被调用者深度排序
        return (caller_depth, callee_depth, caller, callee)
    
    sorted_edges = sorted(all_edges, key=edge_sort_key)
    
    return sorted_edges


def convert_to_compact_format(item):
    """
    转换为紧凑格式，并按调用深度排序
    """
    test_id = item["test-id"]
    
    # 收集所有调用关系
    raw_relations = []
    for relation in item.get("call-relations", []):
        caller = relation["caller"]
        callee = relation["callee"]
        
        caller_id = format_function_identifier(caller)
        callee_id = format_function_identifier(callee)
        
        raw_relations.append((caller_id, callee_id))
    
    # 按深度排序
    sorted_relations = sort_call_relations_by_depth(test_id, raw_relations)
    
    # 格式化为字符串
    edges = [f"{caller} -> {callee}" for caller, callee in sorted_relations]
    
    return test_id, {"call_relations": edges}


def convert_item(item):
    """
    转换单条测试数据：从源格式到目标格式（调用树格式）
    包含nodes和edges，edges按深度排序
    """
    test_id = item["test-id"]
    
    # 收集所有调用关系
    raw_relations = []
    nodes = set()
    
    for relation in item.get("call-relations", []):
        caller = relation["caller"]
        callee = relation["callee"]
        
        caller_id = format_function_identifier(caller)
        callee_id = format_function_identifier(callee)
        
        nodes.add(caller_id)
        nodes.add(callee_id)
        raw_relations.append((caller_id, callee_id))
    
    # 按深度排序
    sorted_relations = sort_call_relations_by_depth(test_id, raw_relations)
    
    # 格式化为字符串
    edges = [f"{caller} -> {callee}" for caller, callee in sorted_relations]
    
    call_tree = {
        "nodes": sorted(list(nodes)),
        "edges": edges
    }
    
    return test_id, call_tree


# Example: python parse_call_graph.py --source_folder ./results --save_folder ./results_call_graph --substring scikit-learn
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert call-relations data to call tree format with depth-based sorting"
    )
    parser.add_argument("--source_folder", type=str, required=True, 
                        help="Path to the input folder containing trace files")
    parser.add_argument("--save_folder", type=str, required=True, 
                        help="Path to the output folder")
    parser.add_argument("--substring", type=str, required=True, 
                        help="Filter substring for project id")
    parser.add_argument("--compact", default=False,
                        help="Use compact format (only edges, no explicit nodes)")
    
    args = parser.parse_args()
    
    # 创建输出文件夹
    os.makedirs(args.save_folder, exist_ok=True)
    
    # 遍历源文件夹中的所有子文件夹
    for sub_folder in tqdm(os.listdir(args.source_folder)):
        if args.substring not in sub_folder:  # 只选择指定的项目
            continue
        
        # 读取源数据
        source_path = os.path.join(args.source_folder, sub_folder, "result", "traces.json")
        
        if not os.path.exists(source_path):
            print(f"Warning: {source_path} does not exist, skipping...")
            continue
        
        with open(source_path, "r") as f:
            source_data = json.load(f)
        
        # 转换数据
        target_data = {}
        for item in source_data:
            if args.compact:
                test_id, converted = convert_to_compact_format(item)
            else:
                test_id, converted = convert_item(item)
            target_data[test_id] = converted
        
        # 生成保存文件名
        save_name = sub_folder.split("_")[-1]  # scikit-learn-25747
        save_name = save_name.rsplit("-", 1)[0] + "__" + save_name + ".json"
        save_path = os.path.join(args.save_folder, save_name)
        
        # 保存结果
        with open(save_path, "w") as f:
            json.dump(target_data, f, indent=2)
        
        print(f"Processed: {sub_folder} -> {save_name}")
    
    print(f"\nConversion complete! Results saved to: {args.save_folder}")
