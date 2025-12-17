# repair_results.py
import os
import ast
from datasets import load_dataset
import git
from tqdm import tqdm
from multiprocessing import Pool
import tempfile
import shutil
import warnings
import logging
from datetime import datetime
import gc
from collections import OrderedDict


try:
    import orjson
    USE_ORJSON = True
except ImportError:
    import json
    USE_ORJSON = False

# 忽略 SyntaxWarning
warnings.filterwarnings('ignore', category=SyntaxWarning)


GLOBAL_RAW_ROOT = None
GLOBAL_TARGET_ROOT = None
GLOBAL_INSTANCE_INFO = None
GLOBAL_ISSUE_MAP = None

def init_worker(raw_root, target_root, instance_info, issue_map):
    """多进程初始化函数"""
    global GLOBAL_RAW_ROOT, GLOBAL_TARGET_ROOT, GLOBAL_INSTANCE_INFO, GLOBAL_ISSUE_MAP
    GLOBAL_RAW_ROOT = raw_root
    GLOBAL_TARGET_ROOT = target_root
    GLOBAL_INSTANCE_INFO = instance_info
    GLOBAL_ISSUE_MAP = issue_map


class ASTLRUCache:
    """带有容量限制的 AST 缓存，防止内存无限增长"""
    def __init__(self, capacity=50):
        self.cache = OrderedDict()
        self.capacity = capacity

    def get(self, key):
        if key not in self.cache:
            return None
        self.cache.move_to_end(key)
        return self.cache[key]

    def put(self, key, value):
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)


# 日志目录
LOG_DIR = "./repair_logs"

def setup_instance_logger(instance_name):
    """为每个实例创建独立的日志记录器"""
    os.makedirs(LOG_DIR, exist_ok=True)
    
    # 清理 instance_name 中的非法文件名字符
    safe_name = instance_name.replace("/", "_").replace("\\", "_")
    log_file = os.path.join(LOG_DIR, f"{safe_name}.log")
    
    # 创建独立的 logger
    logger = logging.getLogger(f"instance_{safe_name}")
    logger.setLevel(logging.DEBUG)
    
    # 避免重复添加 handler
    if logger.handlers:
        return logger
    
    # 文件 handler
    file_handler = logging.FileHandler(log_file, mode='w', encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    
    # 格式化器
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    return logger


def setup_main_logger():
    """设置主日志记录器，记录整体进度"""
    os.makedirs(LOG_DIR, exist_ok=True)
    
    logger = logging.getLogger("main")
    logger.setLevel(logging.DEBUG)
    
    if logger.handlers:
        return logger
    
    # 文件 handler
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(LOG_DIR, f"main_{timestamp}.log")
    file_handler = logging.FileHandler(log_file, mode='w', encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    
    # 控制台 handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

def get_class_at_line(file_path, line_number):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            source = f.read()
        
        tree = ast.parse(source)
        
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                class_start = node.lineno
                class_end = max(
                    getattr(child, 'end_lineno', 0) 
                    for child in ast.walk(node)
                )
                if class_start <= line_number <= class_end:
                    return node.name
    except Exception as e:
        # 静默处理错误，不打印
        pass
    return None


def get_class_at_line_with_cache(file_path, line_number, ast_cache):
    """带 LRU 缓存的类查找函数"""
    # 修改 1: 使用 ast_cache.get() 获取缓存
    tree = ast_cache.get(file_path)
    
    # 如果缓存未命中，则解析文件
    if tree is None:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                source = f.read()
            tree = ast.parse(source)
            # 修改 2: 解析成功，放入缓存
            ast_cache.put(file_path, tree)
        except Exception:
            # 解析失败存为 False，避免重复读取同一错误文件
            ast_cache.put(file_path, False)
            tree = False
    
    # 如果之前标记为解析失败，直接返回 None
    if tree is False:
        return None
    
    # 遍历 AST 查找类名 (逻辑保持不变)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            class_start = node.lineno
            # 计算类的结束行号
            class_end = max(
                getattr(child, 'end_lineno', 0) 
                for child in ast.walk(node)
            )
            # 兼容处理：如果类内部没有子节点或获取不到行号，至少覆盖起始行
            if class_end == 0: 
                class_end = class_start

            if class_start <= line_number <= class_end:
                return node.name
    return None


def process_single_folder(raw_result_folder):
    """处理单个文件夹的函数，动态克隆仓库到临时目录"""
    # [修改] 从全局变量获取配置，替代参数解包
    # 请确保在文件头部定义了这些 GLOBAL_ 变量并在 init_worker 中赋值
    raw_results_root_folder = GLOBAL_RAW_ROOT
    target_results_root_folder = GLOBAL_TARGET_ROOT
    instance_info_map = GLOBAL_INSTANCE_INFO
    issue_to_instance = GLOBAL_ISSUE_MAP
    
    # 初始化实例日志
    logger = setup_instance_logger(raw_result_folder)
    logger.info(f"{'='*60}")
    logger.info(f"开始处理: {raw_result_folder}")
    logger.info(f"{'='*60}")
    
    # 检查是否已存在输出文件，若存在则跳过
    output_file = os.path.join(target_results_root_folder, raw_result_folder, "result", "traces.json")
    if os.path.exists(output_file):
        logger.info(f"输出文件已存在，跳过处理: {output_file}")
        logger.info(f"{'='*60}")
        logger.info("跳过 (已完成)")
        logger.info(f"{'='*60}")
        # 关闭 logger 的 handlers，释放文件句柄
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)
        return f"⊘ {raw_result_folder} (skipped: already exists)"
    
    temp_dir = None
    start_time = datetime.now()
    
    try:
        # [修改] 使用 LRU Cache 替代普通字典，容量设为 50
        # 这样即使文件很多，内存占用也是有上限的
        ast_cache = ASTLRUCache(capacity=50) 
        
        raw_reult_path = os.path.join(raw_results_root_folder, raw_result_folder, "result", "traces.json")
        logger.debug(f"traces.json 路径: {raw_reult_path}")

        # 从文件夹名称解析 instance_name
        issue_part = raw_result_folder.split("_")[-1]
        logger.debug(f"提取的 issue 部分: {issue_part}")
        
        # 通过反向映射表获取完整的 instance_id
        instance_name = issue_to_instance.get(issue_part)
        if instance_name is None:
            logger.error(f"无法通过 issue 部分找到 instance: {issue_part}")
            return f"Error: No instance found for issue {issue_part}"
        logger.info(f"解析得到 instance_name: {instance_name}")

        instance_info = instance_info_map.get(instance_name)
        if instance_info is None:
            logger.error(f"未找到 instance 信息: {instance_name}")
            return f"Error: No instance info found for {instance_name}"

        base_commit = instance_info['base_commit']
        repo = instance_info['repo']
        logger.info(f"仓库: {repo}")
        logger.info(f"目标 commit: {base_commit}")

        # 构建 GitHub URL 并克隆到临时目录
        repo_url = f"https://github.com/{repo}.git"
        temp_dir = tempfile.mkdtemp(prefix=f"repo_{raw_result_folder}_")
        logger.debug(f"临时目录: {temp_dir}")

        # 使用 shallow fetch 只获取特定 commit
        logger.info("开始 shallow fetch...")
        fetch_start = datetime.now()
        git.Repo.init(temp_dir)
        cloned_repo = git.Repo(temp_dir)
        cloned_repo.create_remote('origin', repo_url)
        cloned_repo.git.fetch('--depth=1', 'origin', base_commit)
        cloned_repo.git.checkout('FETCH_HEAD')
        fetch_time = (datetime.now() - fetch_start).total_seconds()
        logger.info(f"shallow fetch 完成，耗时: {fetch_time:.2f}s")
        
        # 读取 traces.json
        logger.info("读取 traces.json...")
        read_start = datetime.now()
        if USE_ORJSON:
            with open(raw_reult_path, "rb") as f:
                raw_results = orjson.loads(f.read())
        else:
            with open(raw_reult_path, "r") as f:
                raw_results = json.load(f)
        read_time = (datetime.now() - read_start).total_seconds()
        logger.info(f"traces.json 读取完成，耗时: {read_time:.2f}s，包含 {len(raw_results)} 个 item")
        
        # 统计信息
        total_relations = sum(len(item.get("call-relations", [])) for item in raw_results)
        logger.info(f"总计需要处理 {total_relations} 个 call-relation")
        
        # 处理每个 item
        processed_relations = 0
        files_parsed = set()
        
        for item_idx, item in enumerate(raw_results):
            relations = item.get("call-relations", [])
            # 减少日志量，只在 debug 模式记录
            if relations:
                logger.debug(f"处理 item[{item_idx}]: {len(relations)} 个 relations")
            
            for relation in relations:
                caller = relation["caller"]
                callee = relation["callee"]

                caller_filepath = os.path.join(temp_dir, caller["filepath"])
                callee_filepath = os.path.join(temp_dir, callee["filepath"])
                
                # 使用带缓存的函数 (ast_cache 现在是 ASTLRUCache 对象)
                caller_class = get_class_at_line_with_cache(
                    caller_filepath, 
                    caller["lineno"],
                    ast_cache
                )
                callee_class = get_class_at_line_with_cache(
                    callee_filepath, 
                    callee["lineno"],
                    ast_cache
                )
                
                caller["class_name"] = caller_class if caller_class else ""
                callee["class_name"] = callee_class if callee_class else ""
                
                files_parsed.add(caller["filepath"])
                files_parsed.add(callee["filepath"])
                processed_relations += 1
        
        logger.info(f"处理完成: {processed_relations} 个 relations")
        logger.info(f"解析了 {len(files_parsed)} 个不同的文件")
        # logger.info(f"AST 缓存命中情况: {len(ast_cache.cache)}") # 可选打印缓存大小

        # 保存修复后的结果
        output_dir = os.path.join(target_results_root_folder, raw_result_folder, "result")
        os.makedirs(output_dir, exist_ok=True)
        save_path = os.path.join(output_dir, "traces.json")
        logger.info(f"保存结果到: {save_path}")
        
        if USE_ORJSON:
            with open(save_path, "wb") as f:
                f.write(orjson.dumps(raw_results, option=orjson.OPT_INDENT_2))
        else:
            with open(save_path, "w") as f:
                json.dump(raw_results, f, indent=4)
        
        total_time = (datetime.now() - start_time).total_seconds()
        logger.info(f"{'='*60}")
        logger.info(f"处理成功! 总耗时: {total_time:.2f}s")
        logger.info(f"{'='*60}")
        
        # [新增] 显式释放大对象内存，帮助 GC
        del raw_results
        del ast_cache

        return f"✓ {raw_result_folder}"
    
    except Exception as e:
        import traceback
        error_msg = str(e)
        stack_trace = traceback.format_exc()
        
        logger.error(f"处理失败: {error_msg}")
        logger.error(f"堆栈跟踪:\n{stack_trace}")
        
        total_time = (datetime.now() - start_time).total_seconds()
        logger.error(f"失败时已耗时: {total_time:.2f}s")
        
        return f"✗ {raw_result_folder}: {error_msg}"
    
    finally:
        # 清理临时目录
        if temp_dir and os.path.exists(temp_dir):
            logger.debug(f"清理临时目录: {temp_dir}")
            shutil.rmtree(temp_dir, ignore_errors=True)
        
        # 关闭 logger 的 handlers
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)
        
        # [新增] 显式垃圾回收，确保进程常驻时内存能回落
        gc.collect()

def process_raw_results(raw_results_root_folder, target_results_root_folder, instance_info_map, issue_to_instance, num_processes=4):
    main_logger = setup_main_logger()
    main_logger.info(f"{'='*60}")
    main_logger.info("开始批量处理任务")
    main_logger.info(f"{'='*60}")
    main_logger.info(f"原始结果目录: {raw_results_root_folder}")
    main_logger.info(f"目标结果目录: {target_results_root_folder}")
    main_logger.info(f"日志目录: {LOG_DIR}")
    
    folders = [f for f in os.listdir(raw_results_root_folder) 
               if os.path.isdir(os.path.join(raw_results_root_folder, f))]
    
    main_logger.info(f"发现 {len(folders)} 个待处理文件夹")
    main_logger.info(f"使用 {num_processes} 个进程")
    
    print(f"Found {len(folders)} folders to process")
    print(f"Using {num_processes} processes")
    
    # [修改] args_list 现在只包含文件夹名称，不再传递整个 map
    args_list = folders
    
    # [修改] 使用 initializer 初始化进程池，设置全局变量
    with Pool(processes=num_processes, 
              initializer=init_worker, 
              initargs=(raw_results_root_folder, target_results_root_folder, instance_info_map, issue_to_instance)) as pool:
        
        # 使用 imap_unordered 以获得更好的进度显示
        results = list(tqdm(
            pool.imap_unordered(process_single_folder, args_list),
            total=len(args_list),
            desc="Processing folders",
            unit="folder",
            # 显示更详细的信息
            bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]'
        ))
    
    # 统计结果
    success_count = sum(1 for r in results if r.startswith("✓"))
    error_count = sum(1 for r in results if r.startswith("✗"))
    skipped_count = sum(1 for r in results if r.startswith("⊘"))
    
    main_logger.info(f"{'='*60}")
    main_logger.info("处理完成!")
    main_logger.info(f"成功: {success_count}/{len(folders)}")
    main_logger.info(f"跳过: {skipped_count}/{len(folders)}")
    main_logger.info(f"失败: {error_count}/{len(folders)}")
    if success_count + skipped_count > 0:
        main_logger.info(f"有效完成率: {(success_count + skipped_count)/len(folders)*100:.1f}%")
    main_logger.info(f"{'='*60}")
    
    print(f"\n{'='*60}")
    print(f"Processing complete!")
    print(f"Success: {success_count}/{len(folders)}")
    print(f"Skipped: {skipped_count}/{len(folders)}")
    print(f"Errors: {error_count}/{len(folders)}")
    print(f"{'='*60}\n")
    
    # 打印跳过信息
    if skipped_count > 0:
        main_logger.info(f"跳过的实例 (已存在): {skipped_count} 个")
        print(f"Skipped (already exists): {skipped_count}")
    
    # 只打印错误信息
    if error_count > 0:
        main_logger.warning("遇到以下错误:")
        print("Errors encountered:")
        for result in results:
            if result.startswith("✗"):
                main_logger.warning(f"  {result}")
                print(f"  {result}")
    
    # 记录所有成功的实例
    main_logger.info("成功处理的实例:")
    for result in results:
        if result.startswith("✓"):
            main_logger.info(f"  {result}")
    
    # 记录所有跳过的实例
    if skipped_count > 0:
        main_logger.info("跳过的实例:")
        for result in results:
            if result.startswith("⊘"):
                main_logger.info(f"  {result}")

if __name__ == '__main__':
    # 构建 instance 信息映射，包含 base_commit 和 repo
    instance_info_map = dict()
    swe_bench_data = load_dataset("/home/jiawei/RepoCodeLoc/swe-bench-lite")
    for item in swe_bench_data['test']:
        instance = item['instance_id']
        instance_info_map[instance] = {
            'base_commit': item['base_commit'],
            'repo': item['repo']  # 格式如 "scikit-learn/scikit-learn"
        }
    
    # 构建反向映射：从 issue 部分 (如 "pylint-7993") 映射到完整 instance_id
    # 用于处理文件夹名无法还原完整 instance_id 的情况（如 pylint-dev/pylint）
    issue_to_instance = {}
    for instance_id in instance_info_map:
        # instance_id 格式: "owner__repo-issue" 如 "pylint-dev__pylint-7993"
        # 提取 issue 部分: "pylint-7993"
        issue_part = instance_id.split("__")[-1]
        issue_to_instance[issue_part] = instance_id

    raw_results_root_folder = "/home/jiawei/RepoCodeLoc/tools/Dynamic_Coverage_Map/results"
    target_results_root_folder = "/home/jiawei/RepoCodeLoc/tools/Dynamic_Coverage_Map/results_repaired"
    
    # 设置进程数
    num_processes = min(os.cpu_count(), 23)
    process_raw_results(raw_results_root_folder, target_results_root_folder, instance_info_map, issue_to_instance, num_processes=num_processes)