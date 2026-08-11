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


def build_call_tree_text(test_id, call_relations):
    """
    将调用关系构建为树形文本结构
    
    参数：
    - test_id: 测试用例ID（作为根节点）
    - call_relations: 调用关系列表 [(caller, callee), ...]
    
    返回：
    - 树形文本字符串
    """
    # 构建邻接表和去重
    graph = defaultdict(list)
    children_set = defaultdict(set)  # 用于去重
    all_nodes = set()
    
    for caller, callee in call_relations:
        if callee not in children_set[caller]:
            graph[caller].append(callee)
            children_set[caller].add(callee)
        all_nodes.add(caller)
        all_nodes.add(callee)
    
    # 如果没有调用关系，返回空树
    if not all_nodes:
        return test_id
    
    # 使用测试ID作为根节点
    root = test_id
    
    # 如果根节点不在图中，尝试找到实际的根节点
    if root not in graph and root not in all_nodes:
        # 找到没有被任何节点调用的节点作为根
        called_nodes = set()
        for caller in graph:
            called_nodes.update(graph[caller])
        potential_roots = all_nodes - called_nodes
        if potential_roots:
            root = sorted(potential_roots)[0]
        elif all_nodes:
            root = sorted(all_nodes)[0]
    
    # 构建树形文本
    lines = []
    visited = set()
    
    def build_tree_recursive(node, prefix="", is_last=True, depth=0):
        """递归构建树形结构"""
        if node in visited:
            # 如果已访问（循环引用），标记出来
            connector = "└── " if is_last else "├── "
            lines.append(prefix + connector + f"{node} [↻ already shown]")
            return
        
        visited.add(node)
        
        # 添加当前节点
        if depth == 0:  # 根节点
            lines.append(node)
        else:
            connector = "└── " if is_last else "├── "
            lines.append(prefix + connector + node)
        
        # 获取子节点
        children = graph.get(node, [])
        if not children:
            return
        
        # 递归处理子节点
        for i, child in enumerate(children):
            is_last_child = (i == len(children) - 1)
            
            if depth == 0:  # 根节点的子节点
                extension = ""
            else:
                extension = "    " if is_last else "│   "
            
            new_prefix = prefix + extension
            build_tree_recursive(child, new_prefix, is_last_child, depth + 1)
    
    # 从根节点开始构建
    build_tree_recursive(root, depth=0)
    
    # 处理未访问的节点（可能存在多个独立的调用树）
    unvisited = sorted(all_nodes - visited)
    if unvisited:
        lines.append("\n# Disconnected nodes:")
        for node in unvisited:
            lines.append("")
            build_tree_recursive(node, depth=0)
    
    return "\n".join(lines)


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


def convert_to_tree_format(item):
    """
    转换为树形文本格式
    
    返回：
    - test_id: 测试ID
    - dict: 包含call_tree和nodes的字典
    """
    test_id = item["test-id"]
    
    # 收集所有调用关系和节点
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
    
    # 构建树形文本
    tree_text = build_call_tree_text(test_id, sorted_relations)
    
    return test_id, {
        "call_tree": tree_text,
        "nodes": sorted(list(nodes))
    }


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


# 使用示例：
# Example: python parse_call_tree.py --source_folder ./results --save_folder ./results_call_tree --substring scikit-learn --format tree
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert call-relations data to tree text format for better LLM understanding"
    )
    parser.add_argument("--source_folder", type=str, required=True, 
                        help="Path to the input folder containing trace files")
    parser.add_argument("--save_folder", type=str, required=True, 
                        help="Path to the output folder")
    parser.add_argument("--substring", type=str, required=True, 
                        help="Filter substring for project id")
    parser.add_argument("--format", type=str, default="tree", 
                        choices=["tree", "compact", "graph"],
                        help="Output format: 'tree' (tree text), 'compact' (edges only), 'graph' (nodes+edges)")
    
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
            if args.format == "tree":
                test_id, converted = convert_to_tree_format(item)
            elif args.format == "compact":
                test_id, converted = convert_to_compact_format(item)
            else:  # graph
                test_id, converted = convert_item(item)
            target_data[test_id] = converted
        
        # 生成保存文件名
        save_name = sub_folder.split("_")[-1]  # scikit-learn-25747
        save_name = save_name.rsplit("-", 1)[0] + "__" + save_name + ".json"
        save_path = os.path.join(args.save_folder, save_name)
        
        # 保存结果
        with open(save_path, "w") as f:
            json.dump(target_data, f, indent=2, ensure_ascii=False)
        
        print(f"Processed: {sub_folder} -> {save_name}")
    
    print(f"\nConversion complete! Results saved to: {args.save_folder}")
    print(f"Format used: {args.format}")
