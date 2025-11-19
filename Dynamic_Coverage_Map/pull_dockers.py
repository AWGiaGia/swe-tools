#!/usr/bin/env python3
"""
SWE-bench Docker镜像批量下载工具
支持使用前缀过滤并批量下载Docker Hub上的swebench镜像
"""

import requests
import subprocess
import sys
import argparse
from typing import List, Optional
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock


class SWEBenchDownloader:
    """SWE-bench Docker镜像下载器"""
    
    def __init__(self, repository: str = "swebench", proxy: Optional[str] = None, 
                 use_docker_search: bool = False):
        self.repository = repository
        self.base_url = "https://hub.docker.com/v2"
        self.use_docker_search = use_docker_search
        
        # 设置代理
        self.proxies = None
        if proxy:
            self.proxies = {
                'http': proxy,
                'https': proxy
            }
        
        # 设置请求session
        self.session = requests.Session()
        if self.proxies:
            self.session.proxies.update(self.proxies)
        
        # 设置超时和重试
        self.timeout = 30
        self.max_retries = 3
        
        # 线程安全的计数器
        self.success_count = 0
        self.fail_count = 0
        self.skip_count = 0
        self.lock = Lock()
        
    def get_all_tags(self, namespace: str = "swebench") -> List[str]:
        """
        获取指定命名空间下所有镜像标签
        
        Args:
            namespace: Docker Hub命名空间，默认为'swebench'
            
        Returns:
            镜像标签列表
        """
        # 如果使用docker命令获取
        if self.use_docker_search:
            return self.get_tags_via_docker(namespace)
        
        # 使用Docker Hub API获取
        return self.get_tags_via_api(namespace)
    
    def get_tags_via_docker(self, namespace: str) -> List[str]:
        """
        通过docker命令获取镜像标签（需要已经拉取过镜像）
        
        Args:
            namespace: Docker Hub命名空间
            
        Returns:
            镜像标签列表
        """
        print(f"使用docker命令获取本地镜像列表...")
        
        try:
            result = subprocess.run(
                ["docker", "images", f"{namespace}/{self.repository}", "--format", "{{.Tag}}"],
                capture_output=True,
                text=True,
                check=True
            )
            
            tags = [tag.strip() for tag in result.stdout.split('\n') if tag.strip() and tag.strip() != '<none>']
            print(f"找到 {len(tags)} 个本地镜像")
            return tags
            
        except subprocess.CalledProcessError as e:
            print(f"执行docker命令失败: {e.stderr}")
            return []
        except FileNotFoundError:
            print("错误: 未找到docker命令，请确保Docker已安装")
            return []
    
    def get_tags_via_api(self, namespace: str) -> List[str]:
        """
        通过Docker Hub API获取镜像标签
        
        Args:
            namespace: Docker Hub命名空间
            
        Returns:
            镜像标签列表
        """
        tags = []
        url = f"{self.base_url}/repositories/{namespace}/{self.repository}/tags"
        params = {"page_size": 100, "page": 1}
        
        print(f"正在从Docker Hub API获取 {namespace}/{self.repository} 的镜像列表...")
        
        retry_count = 0
        while url and retry_count < self.max_retries:
            try:
                response = self.session.get(url, params=params if params else None, timeout=self.timeout)
                response.raise_for_status()
                data = response.json()
                
                # 提取标签名称
                for tag_info in data.get("results", []):
                    tag_name = tag_info.get("name")
                    if tag_name:
                        tags.append(tag_name)
                
                # 获取下一页URL
                url = data.get("next")
                params = None  # 下一页URL已包含参数
                retry_count = 0  # 重置重试计数
                
                print(f"已获取 {len(tags)} 个镜像标签...")
                
            except requests.exceptions.ConnectionError as e:
                retry_count += 1
                print(f"连接错误 (尝试 {retry_count}/{self.max_retries}): {e}")
                
                if retry_count < self.max_retries:
                    wait_time = 2 ** retry_count  # 指数退避
                    print(f"等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)
                else:
                    print("\n" + "="*60)
                    print("网络连接失败！可能的原因和解决方案：")
                    print("="*60)
                    print("1. 网络连接问题")
                    print("   - 检查网络连接是否正常")
                    print("   - 尝试访问 https://hub.docker.com")
                    print()
                    print("2. 需要代理")
                    print("   - 使用 --proxy 参数，例如：")
                    print("     --proxy http://127.0.0.1:7890")
                    print()
                    print("3. 防火墙/DNS问题")
                    print("   - 检查防火墙设置")
                    print("   - 尝试更换DNS服务器")
                    print()
                    print("4. 使用预定义镜像列表")
                    print("   - 使用 --image-file 参数指定镜像列表文件")
                    print("="*60)
                    break
                    
            except requests.exceptions.RequestException as e:
                print(f"请求错误: {e}")
                break
                
        return tags
    
    def filter_tags_by_prefix(self, tags: List[str], prefix: str) -> List[str]:
        """
        根据前缀过滤镜像标签
        
        Args:
            tags: 镜像标签列表
            prefix: 过滤前缀
            
        Returns:
            过滤后的镜像标签列表
        """
        # 移除命名空间前缀（如果存在）
        if prefix.startswith(f"{self.repository}/"):
            prefix = prefix[len(f"{self.repository}/"):]
        
        filtered = [tag for tag in tags if tag.startswith(prefix)]
        return filtered


    def check_image_exists(self, tag: str, namespace: str = "swebench", use_ghcr: bool = False) -> bool:
        """
        检查Docker镜像是否已经存在（检查多种可能的格式）
        
        Args:
            tag: 镜像标签（格式：sweb.eval.x86_64.xxx）
            namespace: Docker Hub命名空间
            use_ghcr: 是否使用 GitHub Container Registry
            
        Returns:
            镜像是否存在
        """
        # 提取 instance_id 用于 GHCR 格式检查
        instance_id = tag.replace("sweb.eval.x86_64.", "")
        
        # 对于 GHCR 格式，需要进行下划线转换（与 pull_image 中的逻辑一致）
        ghcr_instance_id = instance_id
        if tag.startswith("sweb.eval.x86_64."):
            parts = instance_id.split('-', 1)
            if len(parts) == 2:
                repo_part = parts[0]
                issue_part = parts[1]
                
                if '_' in repo_part and '__' not in repo_part:
                    first_underscore = repo_part.find('_')
                    left = repo_part[:first_underscore]
                    right = repo_part[first_underscore+1:]
                    
                    if left and right and left[0].isalpha() and right[0].isalpha():
                        ghcr_instance_id = f"{left}__{right}-{issue_part}"
        
        # 需要检查的所有可能格式
        image_formats = [
            # 标准格式：swebench/sweb.eval.x86_64.xxx:latest（最常见）
            f"{namespace}/{tag}:latest",
            # 旧格式：swebench/swebench:sweb.eval.x86_64.xxx
            f"{namespace}/{self.repository}:{tag}",
            # GHCR 格式（带下划线转换）
            f"ghcr.io/epoch-research/swe-bench.eval.x86_64.{ghcr_instance_id}:latest"
        ]
        
        try:
            for image_name in image_formats:
                result = subprocess.run(
                    ["docker", "images", "-q", image_name],
                    capture_output=True,
                    text=True,
                    check=True
                )
                # 如果有输出，说明镜像存在
                if result.stdout.strip():
                    return True
            return False
        except subprocess.CalledProcessError:
            return False
        except FileNotFoundError:
            print("错误: 未找到docker命令")
            sys.exit(1)


    def pull_image(self, tag: str, namespace: str = "swebench", use_ghcr: bool = False, 
                   retag_to_swebench: bool = True, index: int = 0, total: int = 0) -> bool:
        """
        拉取指定的Docker镜像
        
        Args:
            tag: 镜像标签
            namespace: Docker Hub命名空间
            use_ghcr: 是否使用 GitHub Container Registry (Epoch AI)
            retag_to_swebench: 是否自动重命名为 SWE-bench 格式
            index: 当前索引（用于显示进度）
            total: 总数（用于显示进度）
            
        Returns:
            是否成功
        """
        if use_ghcr:
            # 使用 Epoch AI 的 GitHub Container Registry
            if tag.startswith("sweb.eval.x86_64."):
                instance_id = tag[len("sweb.eval.x86_64."):]
                
                # 如果 instance_id 中只有单个 _，需要转换为 __
                parts = instance_id.split('-', 1)
                if len(parts) == 2:
                    repo_part = parts[0]
                    issue_part = parts[1]
                    
                    if '_' in repo_part and '__' not in repo_part:
                        first_underscore = repo_part.find('_')
                        left = repo_part[:first_underscore]
                        right = repo_part[first_underscore+1:]
                        
                        if left and right and left[0].isalpha() and right[0].isalpha():
                            instance_id = f"{left}__{right}-{issue_part}"
                
                ghcr_image = f"ghcr.io/epoch-research/swe-bench.eval.x86_64.{instance_id}"
            else:
                ghcr_image = f"ghcr.io/epoch-research/{tag}"
            
            image_name = ghcr_image
        else:
            # 使用 Docker Hub
            image_name = f"{namespace}/{self.repository}:{tag}"
        
        progress_prefix = f"[{index}/{total}] " if total > 0 else ""
        print(f"{progress_prefix}正在下载: {image_name}")
        
        try:
            result = subprocess.run(
                ["docker", "pull", image_name],
                capture_output=True,
                text=True,
                check=True
            )
            print(f"{progress_prefix}✓ 成功下载: {image_name}")
            
            # 如果使用 GHCR 且需要重命名
            if use_ghcr and retag_to_swebench:
                # SWE-bench 格式：swebench/sweb.eval.x86_64.xxx:latest
                # 不是 swebench/swebench:sweb.eval.x86_64.xxx
                swebench_image = f"{namespace}/{tag}:latest"
                try:
                    subprocess.run(
                        ["docker", "tag", image_name, swebench_image],
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    print(f"{progress_prefix}  ↳ 已重命名为: {swebench_image}")
                except subprocess.CalledProcessError:
                    print(f"{progress_prefix}  ⚠ 重命名失败，但镜像已下载")
            
            return True
        except subprocess.CalledProcessError as e:
            print(f"{progress_prefix}✗ 下载失败: {image_name}")
            if e.stderr:
                # 只打印错误的第一行，避免刷屏
                error_line = e.stderr.strip().split('\n')[0]
                print(f"{progress_prefix}  错误: {error_line}")
            return False
        except FileNotFoundError:
            print("错误: 未找到docker命令，请确保Docker已安装并在PATH中")
            sys.exit(1)
    
    def download_images(self, docker_prefix: str, namespace: str = "swebench", 
                       dry_run: bool = False, max_downloads: Optional[int] = None,
                       image_file: Optional[str] = None, use_ghcr: bool = False,
                       skip_existing: bool = True, workers: int = 1, 
                       retag_to_swebench: bool = True) -> None:
        """
        批量下载匹配前缀的Docker镜像
        
        Args:
            docker_prefix: 镜像前缀过滤器
            namespace: Docker Hub命名空间
            dry_run: 是否仅列出镜像而不下载
            max_downloads: 最大下载数量限制
            image_file: 镜像列表文件路径（每行一个镜像名）
            use_ghcr: 是否使用 GitHub Container Registry (Epoch AI)
            skip_existing: 是否跳过已存在的镜像（默认True）
            workers: 并行下载的线程数（默认1，最大16）
            retag_to_swebench: 从GHCR下载后是否自动重命名为SWE-bench格式
        """
        # 从文件读取镜像列表
        if image_file:
            all_tags = self.load_tags_from_file(image_file)
        else:
            # 获取所有标签
            all_tags = self.get_all_tags(namespace)
        
        if not all_tags:
            print("未找到任何镜像标签")
            print("\n提示:")
            print("1. 如果无法访问 Docker Hub API，可以使用 --image-file 参数指定镜像列表文件")
            print("2. 或者使用 --use-ghcr 参数从 GitHub Container Registry 下载")
            print("3. 先运行 generate_swebench_list.py 生成镜像列表")
            return
        
        print(f"\n总共找到 {len(all_tags)} 个镜像标签")
        
        # 根据前缀过滤
        filtered_tags = self.filter_tags_by_prefix(all_tags, docker_prefix)
        
        if not filtered_tags:
            print(f"\n未找到匹配前缀 '{docker_prefix}' 的镜像")
            return
        
        print(f"\n找到 {len(filtered_tags)} 个匹配前缀 '{docker_prefix}' 的镜像")
        
        # 检查已存在的镜像
        existing_tags = []
        missing_tags = []
        
        if skip_existing:
            print("\n正在检查已存在的镜像...")
            for tag in filtered_tags:
                # 检查 SWE-bench 格式的镜像是否存在
                if self.check_image_exists(tag, namespace, use_ghcr=False):
                    existing_tags.append(tag)
                else:
                    missing_tags.append(tag)
            
            print(f"✓ 已存在: {len(existing_tags)} 个")
            print(f"✗ 需下载: {len(missing_tags)} 个")
            
            if existing_tags and len(existing_tags) <= 10:
                print("\n已存在的镜像:")
                for tag in existing_tags:
                    print(f"  - {tag}")
            elif existing_tags:
                print(f"\n已存在的镜像（显示前10个）:")
                for tag in existing_tags[:10]:
                    print(f"  - {tag}")
                print(f"  ... 还有 {len(existing_tags) - 10} 个")
        else:
            missing_tags = filtered_tags
            print(f"\n将下载所有 {len(missing_tags)} 个镜像（不跳过已存在的）")
        
        if not missing_tags:
            print("\n✓ 所有镜像都已存在，无需下载！")
            return
        
        print(f"\n需要下载的镜像（显示前10个）:")
        for i, tag in enumerate(missing_tags[:10], 1):
            if use_ghcr:
                print(f"  {i}. ghcr.io/epoch-research/swe-bench.eval.x86_64.{tag.replace('sweb.eval.x86_64.', '')}")
                if retag_to_swebench:
                    print(f"      -> {namespace}/{tag}:latest")
            else:
                print(f"  {i}. {namespace}/{tag}:latest")
        
        if len(missing_tags) > 10:
            print(f"  ... 还有 {len(missing_tags) - 10} 个镜像")
        
        if dry_run:
            print("\n[预览模式] 未实际下载镜像")
            return
        
        # 限制下载数量
        tags_to_download = missing_tags
        if max_downloads and len(tags_to_download) > max_downloads:
            print(f"\n注意: 将只下载前 {max_downloads} 个镜像")
            tags_to_download = tags_to_download[:max_downloads]
        
        # 限制并发数
        workers = min(max(1, workers), 16)  # 1-16之间
        
        # 确认下载
        print(f"\n准备下载 {len(tags_to_download)} 个镜像")
        if use_ghcr:
            print("使用镜像源: GitHub Container Registry (Epoch AI)")
            if retag_to_swebench:
                print("自动重命名: 是（下载后自动重命名为SWE-bench格式）")
        else:
            print(f"使用镜像源: Docker Hub ({namespace}/{self.repository})")
        
        if skip_existing:
            print(f"跳过已存在: 是")
        
        if workers > 1:
            print(f"并行下载: {workers} 个线程")
        
        confirm = input("\n是否继续? (y/n): ").strip().lower()
        
        if confirm != 'y':
            print("已取消下载")
            return
        
        # 重置计数器
        self.success_count = 0
        self.fail_count = 0
        self.skip_count = len(existing_tags) if skip_existing else 0
        
        print("\n" + "="*60)
        print("开始批量下载镜像...")
        print("="*60)
        print()
        
        # 并行下载
        if workers > 1:
            self._parallel_download(tags_to_download, namespace, use_ghcr, 
                                   retag_to_swebench, workers)
        else:
            self._sequential_download(tags_to_download, namespace, use_ghcr, 
                                     retag_to_swebench)
        
        # 输出统计信息
        print("\n" + "="*60)
        print("下载完成!")
        print("="*60)
        if skip_existing:
            print(f"跳过: {self.skip_count} (已存在)")
        print(f"成功: {self.success_count}")
        print(f"失败: {self.fail_count}")
        print(f"总计: {len(filtered_tags)}")
    
    def _sequential_download(self, tags: List[str], namespace: str, 
                            use_ghcr: bool, retag_to_swebench: bool):
        """顺序下载"""
        total = len(tags)
        for i, tag in enumerate(tags, 1):
            if self.pull_image(tag, namespace, use_ghcr, retag_to_swebench, i, total):
                self.success_count += 1
            else:
                self.fail_count += 1
            print()  # 空行分隔
    
    def _parallel_download(self, tags: List[str], namespace: str, 
                          use_ghcr: bool, retag_to_swebench: bool, workers: int):
        """并行下载"""
        total = len(tags)
        
        def download_task(tag, index):
            success = self.pull_image(tag, namespace, use_ghcr, 
                                     retag_to_swebench, index, total)
            with self.lock:
                if success:
                    self.success_count += 1
                else:
                    self.fail_count += 1
            return success
        
        with ThreadPoolExecutor(max_workers=workers) as executor:
            # 提交所有任务
            futures = {
                executor.submit(download_task, tag, i): (tag, i)
                for i, tag in enumerate(tags, 1)
            }
            
            # 等待完成
            for future in as_completed(futures):
                tag, index = futures[future]
                try:
                    future.result()
                except Exception as e:
                    print(f"[{index}/{total}] ✗ 异常: {tag} - {e}")
                    with self.lock:
                        self.fail_count += 1
    
    def load_tags_from_file(self, filepath: str) -> List[str]:
        """
        从文件加载镜像标签列表
        
        Args:
            filepath: 文件路径
            
        Returns:
            镜像标签列表
        """
        print(f"从文件加载镜像列表: {filepath}")
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                tags = [line.strip() for line in f if line.strip() and not line.startswith('#')]
            print(f"从文件加载了 {len(tags)} 个镜像标签")
            return tags
        except FileNotFoundError:
            print(f"错误: 文件不存在 - {filepath}")
            return []
        except Exception as e:
            print(f"读取文件时出错: {e}")
            return []


def main():
    parser = argparse.ArgumentParser(
        description="批量下载SWE-bench Docker镜像",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  # 基本使用（自动跳过已下载的镜像，自动重命名）
  python %(prog)s --prefix sweb.eval.x86_64 --image-file swebench_images.txt --use-ghcr
  
  # 并行下载（4个线程，显著加速）
  python %(prog)s --prefix sweb.eval.x86_64 --image-file swebench_images.txt --use-ghcr --workers 4
  
  # 断点续传：中断后重新运行会自动跳过已下载的
  python %(prog)s --prefix sweb.eval.x86_64 --image-file swebench_images.txt --use-ghcr --workers 8
  
  # 预览模式（查看哪些镜像已存在，哪些需要下载）
  python %(prog)s --prefix sweb.eval.x86_64 --image-file swebench_images.txt --use-ghcr --dry-run
  
  # 强制重新下载所有镜像（不跳过已存在的）
  python %(prog)s --prefix sweb.eval.x86_64 --image-file swebench_images.txt --use-ghcr --no-skip-existing
  
  # 下载但不自动重命名（保留GHCR原始名称）
  python %(prog)s --prefix sweb.eval.x86_64 --image-file swebench_images.txt --use-ghcr --no-retag
  
  # 限制下载数量（测试用）
  python %(prog)s --prefix sweb.eval.x86_64 --image-file swebench_images.txt --use-ghcr --max 5
  
  # 使用代理 + 并行下载
  python %(prog)s --prefix sweb.eval.x86_64 --image-file swebench_images.txt --proxy http://127.0.0.1:7890 --workers 4

注意事项:
  - 并行下载（--workers）建议使用 4-8 个线程，过多可能导致网络问题
  - 从GHCR下载的镜像会自动重命名为SWE-bench格式（可用 --no-retag 禁用）
  - 重命名后的镜像名称格式: swebench/sweb.eval.x86_64.{instance_id}
  - 断点续传功能会检查SWE-bench格式的镜像，自动跳过已下载的
        """
    )
    
    parser.add_argument(
        "--prefix",
        required=True,
        help="Docker镜像前缀过滤器（例如: sweb.eval.x86_64.astropy_1776_astropy）"
    )
    
    parser.add_argument(
        "--namespace",
        default="swebench",
        help="Docker Hub命名空间（默认: swebench）"
    )
    
    parser.add_argument(
        "--repository",
        default="swebench",
        help="Docker Hub仓库名称（默认: swebench）"
    )
    
    parser.add_argument(
        "--proxy",
        help="HTTP/HTTPS代理地址（例如: http://127.0.0.1:7890）"
    )
    
    parser.add_argument(
        "--image-file",
        help="镜像列表文件路径（每行一个镜像标签名）"
    )
    
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="预览模式，仅列出匹配的镜像而不下载"
    )
    
    parser.add_argument(
        "--max",
        type=int,
        metavar="N",
        help="限制最大下载数量"
    )
    
    parser.add_argument(
        "--use-docker-search",
        action="store_true",
        help="使用docker命令获取本地镜像列表（而非Docker Hub API）"
    )
    
    parser.add_argument(
        "--use-ghcr",
        action="store_true",
        help="使用 GitHub Container Registry (Epoch AI) 下载镜像"
    )
    
    parser.add_argument(
        "--no-skip-existing",
        action="store_true",
        help="不跳过已存在的镜像，强制重新下载所有镜像"
    )
    
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        metavar="N",
        help="并行下载的线程数（1-16，默认1）。注意：过多并发可能导致网络问题"
    )
    
    parser.add_argument(
        "--no-retag",
        action="store_true",
        help="从GHCR下载后不自动重命名为SWE-bench格式"
    )
    
    args = parser.parse_args()
    
    # 从环境变量读取代理（如果未通过参数指定）
    proxy = args.proxy
    if not proxy:
        proxy = os.environ.get('http_proxy') or os.environ.get('HTTP_PROXY')
    
    if proxy:
        print(f"使用代理: {proxy}")
    
    # 创建下载器并执行
    downloader = SWEBenchDownloader(
        repository=args.repository,
        proxy=proxy,
        use_docker_search=args.use_docker_search
    )
    
    downloader.download_images(
        docker_prefix=args.prefix,
        namespace=args.namespace,
        dry_run=args.dry_run,
        max_downloads=args.max,
        image_file=args.image_file,
        use_ghcr=args.use_ghcr,
        skip_existing=not args.no_skip_existing,  # 默认跳过已存在的镜像
        workers=args.workers,  # 并行下载线程数
        retag_to_swebench=not args.no_retag  # 默认自动重命名
    )


if __name__ == "__main__":
    main()