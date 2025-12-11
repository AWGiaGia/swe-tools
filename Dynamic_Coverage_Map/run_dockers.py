#!/usr/bin/env python3

# run_dockers.py
"""
自动化Docker批量执行脚本
用于在多个Docker镜像中批量执行trace.py脚本
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import docker


class InstanceLogFilter(logging.Filter):
    """Filter records to only include logs for a specific instance."""

    def __init__(self, instance_id: str):
        super().__init__()
        self.instance_id = instance_id

    def filter(self, record: logging.LogRecord) -> bool:
        record_instance = getattr(record, 'instance_id', None)
        return record_instance == self.instance_id


class DockerBatchRunner:
    """Docker批量执行管理器"""
    
    def __init__(
        self,
        script_dir: str,
        result_base_dir: str,
        image_prefix: str = "swebench/sweb.eval.x86_64.scikit-learn_1776",
        log_dir: str = "./logs",
        parallel: int = 1  # 新增参数
    ):
        """
        初始化批量执行器
        
        Args:
            script_dir: 脚本目录路径（包含trace.py和hooks.py）
            result_base_dir: 结果文件基础目录
            image_prefix: Docker镜像前缀，用于筛选镜像
            log_dir: 日志文件目录
        """
        self.script_dir = os.path.abspath(script_dir)
        self.result_base_dir = os.path.abspath(result_base_dir)
        self.image_prefix = image_prefix
        self.log_dir = os.path.abspath(log_dir)
        self.last_run_skipped = False
        self.log_dir = os.path.abspath(log_dir)
        self.last_run_skipped = False
        self.parallel = parallel  # 新增：保存并行度

        # 创建日志目录
        os.makedirs(self.log_dir, exist_ok=True)
        
        # 设置日志
        self._setup_logging()
        
        # 连接Docker
        try:
            self.client = docker.from_env()
            self.logger.info("成功连接到Docker")
        except Exception as e:
            self.logger.error(f"无法连接到Docker: {e}", exc_info=True)
            sys.exit(1)
        
        # 验证脚本文件存在
        self._validate_scripts()
        
        # 创建结果基础目录
        os.makedirs(self.result_base_dir, exist_ok=True)
        self.logger.info(f"结果基础目录: {self.result_base_dir}")
        
    def _setup_logging(self):
        """设置日志系统"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.batch_timestamp = timestamp
        self.batch_log_dir = os.path.join(self.log_dir, f"batch_run_{timestamp}")
        os.makedirs(self.batch_log_dir, exist_ok=True)
        summary_log_file = os.path.join(self.batch_log_dir, "batch_run.log")
        
        # 创建logger
        self.logger = logging.getLogger("DockerBatchRunner")
        self.logger.setLevel(logging.DEBUG)
        # 清理已有的处理器，避免重复
        for handler in list(self.logger.handlers):
            self.logger.removeHandler(handler)
            handler.close()
        
        # 文件处理器 - 详细日志
        self.file_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        summary_handler = logging.FileHandler(summary_log_file, encoding='utf-8')
        summary_handler.setLevel(logging.DEBUG)
        summary_handler.setFormatter(self.file_formatter)
        
        # 控制台处理器 - 简洁输出
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%H:%M:%S'
        )
        console_handler.setFormatter(console_formatter)
        
        # 添加处理器
        self.logger.addHandler(summary_handler)
        self.logger.addHandler(console_handler)
        
        self.logger.info(f"批次日志目录: {self.batch_log_dir}")
        self.logger.info(f"批次汇总日志: {summary_log_file}")
        self.logger.info("每个实例的日志将保存在该目录下，以实例名称命名")

    def _sanitize_instance_id(self, instance_id: str) -> str:
        """将实例标识符转换为安全的文件名"""
        sanitized = instance_id.replace('/', '__').replace(':', '__').replace(' ', '_')
        return sanitized

    def _attach_instance_log_handler(self, instance_id: str) -> Tuple[logging.FileHandler, str]:
        """
        为指定实例添加独立的日志处理器
        
        Args:
            instance_id: 镜像实例标识符
            
        Returns:
            新增的日志处理器
        """
        sanitized = self._sanitize_instance_id(instance_id)
        instance_log_file = os.path.join(self.batch_log_dir, f"{sanitized}.log")
        try:
            handler = logging.FileHandler(instance_log_file, encoding='utf-8')
            handler.setLevel(logging.DEBUG)
            handler.setFormatter(self.file_formatter)
            handler.addFilter(InstanceLogFilter(instance_id))
            self.logger.addHandler(handler)
            return handler, instance_log_file
        except Exception as e:
            self.logger.error(f"创建实例日志处理器失败: {instance_log_file} -> {e}", exc_info=True)
            raise
    
    def _get_instance_logger(self, instance_id: str) -> logging.LoggerAdapter:
        """创建带有instance_id上下文的LoggerAdapter"""
        return logging.LoggerAdapter(self.logger, {'instance_id': instance_id})
        
    def _validate_scripts(self):
        """验证脚本文件是否存在"""
        required_files = ['trace.py', 'hooks.py']
        missing_files = []
        
        for file in required_files:
            file_path = os.path.join(self.script_dir, file)
            if not os.path.exists(file_path):
                missing_files.append(file)
                
        if missing_files:
            self.logger.error(f"脚本目录缺少必要文件: {', '.join(missing_files)}")
            self.logger.error(f"脚本目录: {self.script_dir}")
            sys.exit(1)
        
        self.logger.info(f"脚本文件验证通过: {self.script_dir}")
        
    def get_target_images(self) -> List[Dict]:
        """
        获取所有目标Docker镜像
        
        Returns:
            镜像信息列表
        """
        self.logger.info(f"正在查找镜像前缀为 '{self.image_prefix}' 的镜像...")
        
        images = []
        try:
            all_images = self.client.images.list()
            self.logger.debug(f"共获取 {len(all_images)} 个Docker镜像供筛选")
            for image in all_images:
                for tag in image.tags:
                    if tag.startswith(self.image_prefix):
                        # 从tag中提取实例名称
                        instance_name = self._extract_instance_identifier(tag)
                        images.append({
                            'tag': tag,
                            'id': image.id,
                            'instance_name': instance_name,
                            'short_id': image.short_id
                        })
            
            self.logger.info(f"找到 {len(images)} 个符合条件的镜像")
            for img in images:
                self.logger.debug(f"  - {img['tag']} ({img['short_id']})")
            
            if not images:
                self.logger.warning("未找到任何符合条件的镜像，请检查 image-prefix")
                
            return images
            
        except Exception as e:
            self.logger.error(f"获取镜像列表失败: {e}", exc_info=True)
            return []
    
    def _is_already_processed(self, result_dir: str, logger: Optional[logging.Logger] = None) -> bool:
        """
        检查结果目录是否已存在且非空
        
        Args:
            result_dir: 结果目录路径
            
        Returns:
            如果已处理返回True，否则False
        """
        log = logger or self.logger
        if not os.path.exists(result_dir):
            log.debug(f"结果目录不存在: {result_dir}")
            return False
        
        # 检查目录是否为空
        try:
            files = os.listdir(result_dir)
            if len(files) > 0:
                log.info(f"结果目录已存在且非空: {result_dir} (包含 {len(files)} 个文件)")
                return True
            log.debug(f"结果目录存在但为空: {result_dir}")
        except Exception as e:
            log.warning(f"检查结果目录失败: {e}", exc_info=True)
        
        return False
    
    def _extract_instance_identifier(self, image_tag: str) -> str:
        """
        从镜像tag中提取实例标识符
        例如: swebench/sweb.eval.x86_64.scikit-learn_1776_scikit-learn-10949:latest
        提取: scikit-learn-10949
        """
        tag_without_digest = image_tag.split('@')[0]
        if ':' in tag_without_digest:
            repo_candidate, possible_tag = tag_without_digest.rsplit(':', 1)
            if possible_tag and possible_tag.lower() != 'latest':
                return possible_tag
            tag_without_digest = repo_candidate
        
        parts = tag_without_digest.split('_')
        if len(parts) >= 2:
            return '_'.join(parts[-2:])  # 取最后两部分
        return parts[-1]
    
    def run_in_container(self, image_info: Dict) -> bool:
        """
        在指定镜像的容器中执行脚本
        
        Args:
            image_info: 镜像信息字典
            
        Returns:
            成功返回True，失败返回False
        """
        image_tag = image_info['tag']
        instance_id = self._extract_instance_identifier(image_tag)
        self.last_run_skipped = False
        
        instance_handler = None
        instance_log_file = ""
        instance_logger: Optional[logging.LoggerAdapter] = None
        try:
            instance_handler, instance_log_file = self._attach_instance_log_handler(instance_id)
            instance_logger = self._get_instance_logger(instance_id)
            instance_logger.info(f"实例日志文件: {instance_log_file}")
        except Exception:
            self.logger.error(f"无法为实例 {instance_id} 创建日志处理器，终止该实例处理")
            return False
        
        log = instance_logger
        
        log.info(f"\n{'='*60}")
        log.info(f"开始处理镜像: {image_tag}")
        log.info(f"实例ID: {instance_id}")
        log.info(f"{'='*60}")
        
        # 准备结果目录
        result_dir = os.path.join(self.result_base_dir, instance_id, "result")
        
        # 检查是否已处理
        if self._is_already_processed(result_dir, logger=log):
            log.info(f"⏭️  跳过已处理的镜像: {instance_id}")
            self.last_run_skipped = True
            return True
        
        # 创建结果目录
        os.makedirs(result_dir, exist_ok=True)
        log.info(f"结果目录: {result_dir}")
        
        container = None
        container_name = f"batch-run-{instance_id}-{int(time.time())}"
        
        try:
            # 容器配置
            volumes = {
                result_dir: {'bind': '/workspace/result', 'mode': 'rw'},
                self.script_dir: {'bind': '/host_scripts', 'mode': 'ro'}
            }
            
            log.info(f"创建容器: {container_name}")
            log.debug(f"挂载卷: {volumes}")
            
            # 创建并启动容器
            container = self.client.containers.run(
                image_tag,
                command='/bin/bash',
                name=container_name,
                detach=True,
                stdin_open=True,
                tty=True,
                volumes=volumes,
                pid_mode='host',
                remove=False  # 不自动删除，便于调试
            )
            
            log.info(f"✓ 容器已创建: {container.short_id}")
            
            # 等待容器启动
            time.sleep(2)
            
            # 执行命令序列
            # 使用 conda run 在 testbed 环境中执行命令，确保使用正确的 Python 版本
            # commands = [
            #     # 在 testbed 环境中安装依赖
            #     "conda run -n testbed bash -c 'unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && pip install pytest-json-report pytest-cov'",
            #     # 在 testbed 环境中运行脚本
            #     "conda run -n testbed bash -c 'cd /host_scripts && python trace.py --project-root /testbed --max-workers 16 --output-dir /workspace/result'"
            # ]
            
            # 为了修补pytest仓库上的运行问题，而进行的兼容修改
            commands = [
                # 步骤1：检测是否存在pytest源码
                r"test -f /testbed/src/_pytest/__init__.py && echo 'has_pytest_src' > /tmp/pytest_check.txt || echo 'no_pytest_src' > /tmp/pytest_check.txt",
                
                # 步骤2：根据检测结果安装依赖
                r"""conda run -n testbed bash -c 'unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY; CHECK=$(cat /tmp/pytest_check.txt); if [ "$CHECK" = "has_pytest_src" ]; then echo 检测到testbed中有pytest源码,安装兼容旧版本的插件; pip install pytest-json-report==1.5.0 pytest-metadata==2.0.4 pytest-cov==2.12.1; else echo 未检测到pytest源码,使用默认安装; pip install pytest-json-report pytest-cov; fi'""",
                
                # 步骤3：运行脚本
                "conda run -n testbed bash -c 'cd /host_scripts && python trace.py --project-root /testbed --max-workers 16 --output-dir /workspace/result'"
            ]

            for i, cmd in enumerate(commands, 1):
                log.info(f"执行命令 {i}/{len(commands)}: {cmd[:80]}...")
                
                try:
                    exit_code, output = container.exec_run(
                        f'/bin/bash -c "{cmd}"',
                        stdout=True,
                        stderr=True,
                        demux=True
                    )
                    
                    # 解析输出
                    stdout_output = output[0].decode('utf-8') if output[0] else ""
                    stderr_output = output[1].decode('utf-8') if output[1] else ""
                    
                    # 对于最后一个命令（trace.py执行），记录更详细的信息
                    if i == len(commands):
                        log.info(f"========== trace.py执行输出（完整） ==========")
                        if stdout_output:
                            log.info(f"STDOUT:\n{stdout_output}")
                        if stderr_output:
                            log.info(f"STDERR:\n{stderr_output}")
                        log.info(f"=" * 60)
                    else:
                        # 其他命令保持原有的DEBUG级别
                        if stdout_output:
                            log.debug(f"STDOUT:\n{stdout_output}")
                        if stderr_output:
                            log.debug(f"STDERR:\n{stderr_output}")
                    
                    if exit_code != 0:
                        log.error(f"命令执行失败 (退出码: {exit_code})")
                        log.error(f"命令: {cmd}")
                        if stderr_output:
                            log.error(f"错误信息:\n{stderr_output}")
                        return False
                    
                    log.info(f"✓ 命令 {i} 执行成功")
                    
                except Exception as e:
                    log.error(f"执行命令时发生异常: {e}", exc_info=True)
                    return False
            
            log.info(f"✅ 镜像 {instance_id} 处理完成")
            return True
            
        except docker.errors.ImageNotFound:
            log.error(f"镜像不存在: {image_tag}", exc_info=True)
            return False
        except docker.errors.APIError as e:
            log.error(f"Docker API错误: {e}", exc_info=True)
            return False
        except Exception as e:
            log.error(f"处理镜像时发生未知错误: {e}", exc_info=True)
            return False
        finally:
            active_logger = log or self.logger
            # 清理容器
            if container:
                try:
                    active_logger.info(f"停止并删除容器: {container_name}")
                    container.stop(timeout=10)
                    container.remove()
                    active_logger.debug(f"容器已清理: {container_name}")
                except Exception as e:
                    active_logger.warning(f"清理容器时发生错误: {e}", exc_info=True)
            if instance_handler:
                try:
                    active_logger.debug("移除实例日志处理器")
                    self.logger.removeHandler(instance_handler)
                    instance_handler.close()
                except Exception as e:
                    # 使用warning记录，避免影响其余实例
                    active_logger.warning(f"关闭实例日志处理器失败: {e}", exc_info=True)
    
    def run_batch(self, max_containers: Optional[int] = None):
        """
        批量运行所有目标镜像
        
        Args:
            max_containers: 最大处理容器数量，None表示处理所有
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading
        
        images = self.get_target_images()
        
        if not images:
            self.logger.warning("没有找到符合条件的镜像")
            return
        
        total = len(images)
        original_total = total
        if max_containers:
            total = min(total, max_containers)
            images = images[:max_containers]
            self.logger.info(f"--max 参数设置为 {max_containers}，本次将处理 {total}/{original_total} 个镜像")
        
        # 确定实际并行度
        actual_parallel = min(self.parallel, total)
        
        self.logger.info(f"\n{'='*60}")
        self.logger.info(f"开始批量处理，共 {total} 个镜像")
        if actual_parallel > 1:
            self.logger.info(f"并行度: {actual_parallel} 个容器同时处理")
        else:
            self.logger.info(f"串行模式（并行度: 1）")
        self.logger.info(f"{'='*60}\n")
        
        # 线程安全的计数器
        success_count = 0
        failed_count = 0
        skipped_count = 0
        count_lock = threading.Lock()
        
        start_time = time.time()
        
        def process_image_wrapper(idx_and_info):
            """包装函数，用于并行执行"""
            idx, image_info = idx_and_info
            nonlocal success_count, failed_count, skipped_count
            
            instance_id = self._extract_instance_identifier(image_info['tag'])
            instance_log_file = os.path.join(
                self.batch_log_dir, 
                f"{self._sanitize_instance_id(instance_id)}.log"
            )
            
            self.logger.info(f"\n进度: [{idx}/{total}] - 实例: {instance_id}")
            
            result = self.run_in_container(image_info)
            
            # 线程安全地更新计数器
            with count_lock:
                if result:
                    if self.last_run_skipped:
                        skipped_count += 1
                        self.logger.info(f"实例 {instance_id} 已跳过，日志: {instance_log_file}")
                    else:
                        success_count += 1
                        self.logger.info(f"实例 {instance_id} 处理完成，日志: {instance_log_file}")
                else:
                    failed_count += 1
                    self.logger.error(f"实例 {instance_id} 处理失败，详见日志: {instance_log_file}")
            
            return result
        
        # 串行或并行处理
        if actual_parallel == 1:
            # 串行模式（保持原有行为）
            for idx, image_info in enumerate(images, 1):
                process_image_wrapper((idx, image_info))
        else:
            # 并行模式
            with ThreadPoolExecutor(max_workers=actual_parallel) as executor:
                # 提交所有任务
                futures = {
                    executor.submit(process_image_wrapper, (idx, image_info)): (idx, image_info)
                    for idx, image_info in enumerate(images, 1)
                }
                
                # 等待完成（保持提交顺序的进度显示）
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        idx, image_info = futures[future]
                        instance_id = self._extract_instance_identifier(image_info['tag'])
                        self.logger.error(f"处理实例 {instance_id} 时发生未捕获异常: {e}", exc_info=True)
                        with count_lock:
                            failed_count += 1
        
        # 统计总结
        elapsed_time = time.time() - start_time
        
        self.logger.info(f"\n{'='*60}")
        self.logger.info("批量处理完成")
        self.logger.info(f"{'='*60}")
        self.logger.info(f"总计: {total} 个镜像")
        self.logger.info(f"✅ 成功: {success_count}")
        self.logger.info(f"⏭️  跳过: {skipped_count}")
        self.logger.info(f"❌ 失败: {failed_count}")
        self.logger.info(f"⏱️  总耗时: {elapsed_time:.2f} 秒 ({elapsed_time/60:.2f} 分钟)")
        if actual_parallel > 1:
            self.logger.info(f"⚡ 平均每个容器: {elapsed_time/total:.2f} 秒")
        
        if failed_count > 0:
            self.logger.warning(f"\n有 {failed_count} 个镜像处理失败，请查看日志文件了解详情")

def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='自动化在多个Docker镜像中批量执行脚本',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  python docker_batch_runner.py \\
    --script-dir /home/jiawei/RepoCodeLoc/tools/Dynamic_Coverage_Map/utils \\
    --result-dir /home/jiawei/RepoCodeLoc/tools/Dynamic_Coverage_Map/sklearn-swe-bench \\
    --image-prefix swebench/sweb.eval.x86_64.scikit-learn_1776

  # 只处理前5个镜像（用于测试）
  python docker_batch_runner.py --script-dir /path/to/scripts --result-dir /path/to/results --max 5
        """
    )
    
    parser.add_argument(
        '--script-dir',
        required=True,
        help='脚本目录路径（包含trace.py和hooks.py）'
    )
    
    parser.add_argument(
        '--result-dir',
        required=True,
        help='结果文件基础目录'
    )
    
    parser.add_argument(
        '--image-prefix',
        # default='swebench/sweb.eval.x86_64.scikit-learn_1776',
        default = 'ghcr.io/epoch-research/swe-bench.eval.x86_64',
        help='Docker镜像前缀（默认: swebench/sweb.eval.x86_64.scikit-learn_1776）'
    )
    
    parser.add_argument(
        '--log-dir',
        default='./logs',
        help='日志文件目录（默认: ./logs）'
    )
    
    parser.add_argument(
        '--max',
        type=int,
        default=None,
        help='最大处理镜像数量（用于测试，默认处理所有镜像）'
    )

    parser.add_argument(
        '--parallel',
        type=int,
        default=8,
        help='并行处理的容器数量（默认: 1，建议: 4-8）'
    )

    args = parser.parse_args()
    
    # 验证目录存在
    if not os.path.exists(args.script_dir):
        print(f"错误: 脚本目录不存在: {args.script_dir}")
        sys.exit(1)
    
    # 创建并运行批处理器
    runner = DockerBatchRunner(
        script_dir=args.script_dir,
        result_base_dir=args.result_dir,
        image_prefix=args.image_prefix,
        log_dir=args.log_dir,
        parallel=args.parallel  # 新增参数
    )
    
    runner.run_batch(max_containers=args.max)


if __name__ == "__main__":
    main()
