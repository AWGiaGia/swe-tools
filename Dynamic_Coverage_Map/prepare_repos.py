# 准备代码仓库和对应的docker环境
import json
import os
import subprocess
from multiprocessing import Pool
from pathlib import Path
from functools import partial
import time


from datasets import load_dataset



# Docker镜像名映射表 - 根据repo确定对应的ID
REPO_DOCKER_MAPPING = {
    "scikit-learn/scikit-learn": "scikit-learn_1776_",
    # 可以根据需要添加更多映射
}


# 加载（本地的）swe-bench数据
def load_swebench_data(local_swe_bench_path):
    swe_bench = load_dataset(local_swe_bench_path)

    swe_bench_data = []

    if "validation" in swe_bench:
        for item in swe_bench['validation']:
            swe_bench_data.append(item)
    if "test" in swe_bench:
        for item in swe_bench['test']:
            swe_bench_data.append(item)
    if "train" in swe_bench:
        for item in swe_bench['train']:
            swe_bench_data.append(item)   
    
    # # debug
    # with open("swe_bench_data.json", "w") as f:
    #     json.dump(list(swe_bench_data), f, indent=4)

    # ------------------------------ debug setting
    # degbue_swe_bench_data = []
    # for item in swe_bench_data:
    #     if item['instance_id'] not in ["scikit-learn__scikit-learn-10297", "scikit-learn__scikit-learn-10508"]:
    #         continue
    #     degbue_swe_bench_data.append(item)
    # return degbue_swe_bench_data
    # ------------------------------

    return swe_bench_data



# 根据swe-bench给出的仓库名和commit-base，获取对应的代码仓库，格式为：
# |- scikit-learn__scikit-learn-14894/
# |----| local_repo/ 
# |----|----| .... <content of repo>
# |----| result/
# |----|----| .... <content of coverage result. (For now it is empty)>
# |- scikit-learn__scikit-learn-11281/
# |----| local_repo/ 
# |----|----| .... <content of repo>
# |----| result/
# |----|----| .... <content of coverage result. (For now it is empty)>
# .........
def prepare_single_repo(item, target_dir):
    repo = item['repo']
    instance_id = item['instance_id']
    base_commit = item['base_commit']
    
    # 创建目录结构
    instance_dir = os.path.join(target_dir, instance_id)
    local_repo_dir = os.path.join(instance_dir, "local_repo")
    result_dir = os.path.join(instance_dir, "result")
    
    # 如果已存在则跳过
    if os.path.exists(local_repo_dir):
        print(f"Skipping {instance_id} - already exists")
        return
        
    try:
        # 创建目录
        os.makedirs(instance_dir, exist_ok=True)
        os.makedirs(result_dir, exist_ok=True)

        # 克隆仓库
        repo_url = f"git@github.com:{repo}.git"
        subprocess.run(['git', 'clone', '--depth', '1', repo_url, local_repo_dir], 
                     check=True, capture_output=True, text=True)
        
        # 获取完整历史并切换到指定commit
        subprocess.run(['git', 'fetch', '--unshallow'], 
                     cwd=local_repo_dir, check=True, capture_output=True, text=True)
        subprocess.run(['git', 'checkout', base_commit], 
                     cwd=local_repo_dir, check=True, capture_output=True, text=True)
        
        print(f"✓ Successfully prepared {instance_id}")
        
    except subprocess.CalledProcessError as e:
        print(f"✗ Error preparing {instance_id}: {e.stderr}")
        # 清理失败的目录
        if os.path.exists(local_repo_dir):
            subprocess.run(['rm', '-rf', local_repo_dir], check=False)
    except Exception as e:
        print(f"✗ Unexpected error for {instance_id}: {e}")

def prepare_repos(swe_bench_data, target_dir):
    # 使用 partial 函数固定 target_dir 参数
    worker_func = partial(prepare_single_repo, target_dir=target_dir)
    
    # 使用多进程处理
    with Pool() as pool:
        pool.map(worker_func, swe_bench_data)



def get_docker_image_name(repo, instance_id):
    """根据repo和instance_id构建docker镜像名"""
    # 从instance_id中提取后缀部分
    # 例如: scikit-learn__scikit-learn-10297 -> scikit-learn-10297
    parts = instance_id.split('__')
    if len(parts) >= 2:
        suffix = parts[1]
    else:
        suffix = instance_id
    
    # 获取repo对应的docker映射
    if repo in REPO_DOCKER_MAPPING:
        docker_middle = REPO_DOCKER_MAPPING[repo]
        image_name = f"swebench/sweb.eval.x86_64.{docker_middle}{suffix}"
    else:
        # 默认规则，可能需要根据实际情况调整
        repo_name = repo.split('/')[1] if '/' in repo else repo
        image_name = f"swebench/sweb.eval.x86_64.{repo_name}_{suffix}"
    
    return image_name

def prepare_single_docker(item, prepared_repos_root):
    """为单个仓库准备docker环境"""
    repo = item['repo']
    instance_id = item['instance_id']
    
    try:
        # 构建docker镜像名
        image_name = get_docker_image_name(repo, instance_id)
        
        # 拉取docker镜像
        print(f"Pulling docker image: {image_name}")
        result = subprocess.run(['docker', 'pull', image_name], 
                              capture_output=True, text=True, timeout=1200)
        
        if result.returncode != 0:
            print(f"✗ Failed to pull {image_name}: {result.stderr}")
            return False
        
        # 构建挂载路径
        instance_dir = os.path.join(prepared_repos_root, instance_id)
        local_repo_path = os.path.join(instance_dir, "local_repo")
        result_path = os.path.join(instance_dir, "result")
        
        # 检查路径是否存在
        if not os.path.exists(local_repo_path):
            print(f"✗ Local repo path not found: {local_repo_path}")
            return False
        
        # 构建容器名
        container_name = f"swebench-{instance_id.replace('__', '-').replace('_', '-')}"
        
        # 停止并删除同名容器（如果存在）
        subprocess.run(['docker', 'stop', container_name], 
                      capture_output=True, text=True)
        subprocess.run(['docker', 'rm', container_name], 
                      capture_output=True, text=True)
        
        # 启动容器
        docker_cmd = [
            'docker', 'run', '-d', '--pid=host',
            '--name', container_name,
            '-v', f"{local_repo_path}:/workspace/local_repo",
            '-v', f"{result_path}:/workspace/result",
            image_name,
            'tail', '-f', '/dev/null'  # 保持容器运行
        ]
        
        # raise ValueError('\n' + ' '.join(docker_cmd), '\n')

        result = subprocess.run(docker_cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"✗ Failed to start container {container_name}: {result.stderr}")
            return False
        
        print(f"✓ Successfully prepared docker for {instance_id}")
        return True
        
    except subprocess.TimeoutExpired:
        print(f"✗ Timeout while preparing docker for {instance_id}")
        return False
    except Exception as e:
        print(f"✗ Unexpected error for {instance_id}: {e}")
        return False

# 为每个仓库下载指定的docker，并启动容器，将代码仓库挂载到容器内的/workspace/local_repo目录，将结果输出路径挂载到容器内的/workspace/result目录。
# 容器命名与Issue名相同，如scikit-learn__scikit-learn-14894
def prepare_dockers(prepared_repos_root):
    """
为每个仓库下载指定的docker，并启动容器，将代码仓库挂载到容器内的/workspace/local_repo目录，
将结果输出路径挂载到容器内的/workspace/result目录。
容器命名与Issue名相同，如scikit-learn__scikit-learn-14894

拉取docker命令示例：
- docker pull swebench/sweb.eval.x86_64.scikit-learn_1776_scikit-learn-10297:v2
- docker pull swebench/sweb.eval.x86_64.scikit-learn_1776_scikit-learn-13779:v2
上述两个命令分别对应instance_id为：
- scikit-learn__scikit-learn-10297
- scikit-learn__scikit-learn-13779

运行docker命令示例：
docker run -it --pid=host \
--name swebench-sklearn-10297 \
-v /home/jiawei/RepoCodeLoc/swe-bench-aw-explore/scikit-learn__scikit-learn-10297/local_repo:/workspace/local_repo \
-v /home/jiawei/RepoCodeLoc/swe-bench-aw-explore/scikit-learn__scikit-learn-10297/result:/workspace/result \
--rm \
swebench/sweb.eval.x86_64.scikit-learn_1776_scikit-learn-10297:latest \
/bin/bash
其中，/home/jiawei/RepoCodeLoc/swe-bench-aw-explore是prepared_repos_root路径，swebench/sweb.eval.x86_64.scikit-learn_1776_scikit-learn-10297:latest为本地的镜像名称
    """

    # 获取所有准备好的仓库目录
    instance_dirs = [d for d in os.listdir(prepared_repos_root) 
                    if os.path.isdir(os.path.join(prepared_repos_root, d))]
    
    if not instance_dirs:
        print("No prepared repositories found")
        return
    
    # 构建item列表 (简化版，只包含必要信息)
    items = []
    for instance_id in instance_dirs:
        # 从instance_id推断repo信息
        repo_part = instance_id.split('__')[0] if '__' in instance_id else instance_id
        repo = f"{repo_part}/{repo_part}"  # 简化的repo格式
        items.append({'repo': repo, 'instance_id': instance_id})
    
    # 使用多进程处理
    worker_func = partial(prepare_single_docker, prepared_repos_root=prepared_repos_root)
    
    print(f"Preparing dockers for {len(items)} repositories...")
    with Pool() as pool:
        results = pool.map(worker_func, items)
    
    success_count = sum(results)
    print(f"Docker preparation completed: {success_count}/{len(items)} successful")


def verify_single_docker(instance_id):
    """验证单个docker容器的可用性"""
    container_name = f"swebench-{instance_id.replace('__', '-').replace('_', '-')}"
    
    try:
        # 检查容器是否运行
        result = subprocess.run(['docker', 'ps', '--filter', f'name={container_name}', '--format', '{{.Names}}'],
                              capture_output=True, text=True)
        
        if container_name not in result.stdout:
            print(f"✗ Container {container_name} is not running")
            return False
        
        # 在容器内执行简单的python指令
        test_cmd = ['docker', 'exec', container_name, 'python', '-c', 'print("Hello from container")']
        result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode != 0:
            print(f"✗ Python test failed in {container_name}: {result.stderr}")
            return False
        
        # 检查工作目录是否存在
        check_dirs_cmd = ['docker', 'exec', container_name, 'ls', '/workspace']
        result = subprocess.run(check_dirs_cmd, capture_output=True, text=True)
        
        if 'local_repo' not in result.stdout or 'result' not in result.stdout:
            print(f"✗ Required directories not found in {container_name}")
            return False
        
        print(f"✓ Container {container_name} verification passed")
        return True
        
    except subprocess.TimeoutExpired:
        print(f"✗ Timeout while verifying {container_name}")
        return False
    except Exception as e:
        print(f"✗ Error verifying {container_name}: {e}")
        return False

# 检查docker容器的可用性，这个在prepare_dockers中被使用
def verify_dockers():
    """
    检查docker容器的可用性，在容器内执行简单的python指令
    """
    # 获取所有运行中的swebench容器
    result = subprocess.run(['docker', 'ps', '--filter', 'name=swebench-', '--format', '{{.Names}}'],
                          capture_output=True, text=True)
    
    if result.returncode != 0:
        print("Failed to list docker containers")
        return
    
    container_names = result.stdout.strip().split('\n')
    container_names = [name for name in container_names if name.startswith('swebench-')]
    
    if not container_names:
        print("No swebench containers found")
        return
    
    # 从容器名提取instance_id
    instance_ids = []
    for container_name in container_names:
        # swebench-scikit-learn-scikit-learn-10297 -> scikit-learn__scikit-learn-10297
        instance_id = container_name.replace('swebench-', '').replace('-', '__', 1).replace('-', '_')
        instance_ids.append(instance_id)
    
    print(f"Verifying {len(instance_ids)} docker containers...")
    
    # 使用多进程验证
    with Pool() as pool:
        results = pool.map(verify_single_docker, instance_ids)
    
    success_count = sum(results)
    print(f"Docker verification completed: {success_count}/{len(instance_ids)} passed")



def build_single_repo(instance_id):
    """在单个容器内构建repo环境"""
    container_name = f"swebench-{instance_id.replace('__', '-').replace('_', '-')}"
    
    try:
        # 切换到local_repo目录并运行pip install -e .
        install_cmd = [
            'docker', 'exec', '-w', '/workspace/local_repo', 
            container_name, 'pip', 'install', '-e', '.'
        ]
        
        print(f"Building repo environment for {instance_id}...")
        result = subprocess.run(install_cmd, capture_output=True, text=True, timeout=600)
        
        if result.returncode != 0:
            print(f"✗ Failed to build repo for {instance_id}: {result.stderr}")
            return False
        
        # 验证安装是否成功
        verify_cmd = ['docker', 'exec', container_name, 'python', '-c', 
                     'import sys; print("Python path:"); [print(p) for p in sys.path]']
        result = subprocess.run(verify_cmd, capture_output=True, text=True)
        
        print(f"✓ Successfully built repo environment for {instance_id}")
        return True
        
    except subprocess.TimeoutExpired:
        print(f"✗ Timeout while building repo for {instance_id}")
        return False
    except Exception as e:
        print(f"✗ Error building repo for {instance_id}: {e}")
        return False

# 在每一个容器内，切换到local_repo路径，并运行pip install -e . 指令来构建环境，这个在prepare_dockers中被使用
def build_repo():
    """
    在每一个容器内，切换到local_repo路径，并运行pip install -e . 指令来构建环境
    """
    # 获取所有运行中的swebench容器
    result = subprocess.run(['docker', 'ps', '--filter', 'name=swebench-', '--format', '{{.Names}}'],
                          capture_output=True, text=True)
    
    if result.returncode != 0:
        print("Failed to list docker containers")
        return
    
    container_names = result.stdout.strip().split('\n')
    container_names = [name for name in container_names if name.startswith('swebench-')]
    
    if not container_names:
        print("No swebench containers found")
        return
    
    # 从容器名提取instance_id
    instance_ids = []
    for container_name in container_names:
        # swebench-scikit-learn-scikit-learn-10297 -> scikit-learn__scikit-learn-10297
        instance_id = container_name.replace('swebench-', '').replace('-', '__', 1).replace('-', '_')
        instance_ids.append(instance_id)
    
    print(f"Building repo environments for {len(instance_ids)} containers...")
    
    # 使用多进程构建
    with Pool() as pool:
        results = pool.map(build_single_repo, instance_ids)
    
    success_count = sum(results)
    print(f"Repo building completed: {success_count}/{len(instance_ids)} successful")



if __name__ == '__main__':
    swe_bench_data = load_swebench_data("/home/jiawei/Agentless/sklearn_swe-bench_lite")

    prepare_repos(swe_bench_data, "./sklearn-swe-bench")
    prepare_dockers('./sklearn-swe-bench')