#!/usr/bin/env python3
"""
自动化Docker批量执行脚本
用于在多个Docker镜像中批量执行trace.py脚本
"""

import docker
import os
import sys
import logging
from datetime import datetime
from pathlib import Path
import json
import time
from typing import List, Dict, Optional
import argparse


class DockerBatchRunner:
    """Docker批量执行管理器"""
    
    def __init__(
        self,
        script_dir: str,
        result_base_dir: str,
        image_prefix: str = "swebench/sweb.eval.x86_64.scikit-learn_1776",
        log_dir: str = "./logs"
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
        self.log_dir = log_dir
        
        # 创建日志目录
        os.makedirs(self.log_dir, exist_ok=True)
        
        # 设置日志
        self._setup_logging()
        
        # 连接Docker
        try:
            self.client = docker.from_env()
            self.logger.info("成功连接到Docker")
        except Exception as e:
            self.logger.error(f"无法连接到Docker: {e}")
            sys.exit(1)
        
        # 验证脚本文件存在
        self._validate_scripts()
        
        # 创建结果基础目录
        os.makedirs(self.result_base_dir, exist_ok=True)
        
    def _setup_logging(self):
        """设置日志系统"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = os.path.join(self.log_dir, f"batch_run_{timestamp}.log")
        
        # 创建logger
        self.logger = logging.getLogger("DockerBatchRunner")
        self.logger.setLevel(logging.DEBUG)
        
        # 文件处理器 - 详细日志
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        
        # 控制台处理器 - 简洁输出
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%H:%M:%S'
        )
        console_handler.setFormatter(console_formatter)
        
        # 添加处理器
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        
        self.logger.info(f"日志文件: {log_file}")
        
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
            for image in all_images:
                for tag in image.tags:
                    if tag.startswith(self.image_prefix):
                        # 从tag中提取实例名称
                        # 例如: swebench/sweb.eval.x86_64.scikit-learn_1776_scikit-learn-10949
                        instance_name = tag.split(':')[0].split('_')[-1]  # 提取最后的数字部分
                        images.append({
                            'tag': tag,
                            'id': image.id,
                            'instance_name': instance_name,
                            'short_id': image.short_id
                        })
            
            self.logger.info(f"找到 {len(images)} 个符合条件的镜像")
            for img in images:
                self.logger.debug(f"  - {img['tag']} ({img['short_id']})")
                
            return images
            
        except Exception as e:
            self.logger.error(f"获取镜像列表失败: {e}")
            return []
    
    def _is_already_processed(self, result_dir: str) -> bool:
        """
        检查结果目录是否已存在且非空
        
        Args:
            result_dir: 结果目录路径
            
        Returns:
            如果已处理返回True，否则False
        """
        if not os.path.exists(result_dir):
            return False
        
        # 检查目录是否为空
        try:
            files = os.listdir(result_dir)
            if len(files) > 0:
                self.logger.info(f"结果目录已存在且非空: {result_dir} (包含 {len(files)} 个文件)")
                return True
        except Exception as e:
            self.logger.warning(f"检查结果目录失败: {e}")
        
        return False
    
    def _extract_instance_identifier(self, image_tag: str) -> str:
        """
        从镜像tag中提取实例标识符
        例如: swebench/sweb.eval.x86_64.scikit-learn_1776_scikit-learn-10949:latest
        提取: scikit-learn-10949
        """
        tag_without_latest = image_tag.split(':')[0]
        parts = tag_without_latest.split('_')
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
        
        self.logger.info(f"\n{'='*60}")
        self.logger.info(f"开始处理镜像: {image_tag}")
        self.logger.info(f"实例ID: {instance_id}")
        self.logger.info(f"{'='*60}")
        
        # 准备结果目录
        result_dir = os.path.join(self.result_base_dir, instance_id, "result")
        
        # 检查是否已处理
        if self._is_already_processed(result_dir):
            self.logger.info(f"⏭️  跳过已处理的镜像: {instance_id}")
            return True
        
        # 创建结果目录
        os.makedirs(result_dir, exist_ok=True)
        self.logger.info(f"结果目录: {result_dir}")
        
        container = None
        container_name = f"batch-run-{instance_id}-{int(time.time())}"
        
        try:
            # 容器配置
            volumes = {
                result_dir: {'bind': '/workspace/result', 'mode': 'rw'},
                self.script_dir: {'bind': '/host_scripts', 'mode': 'ro'}
            }
            
            self.logger.info(f"创建容器: {container_name}")
            self.logger.debug(f"挂载卷: {volumes}")
            
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
            
            self.logger.info(f"✓ 容器已创建: {container.short_id}")
            
            # 等待容器启动
            time.sleep(2)
            
            # 执行命令序列
            # 使用 conda run 在 testbed 环境中执行命令，确保使用正确的 Python 版本
            commands = [
                # 在 testbed 环境中安装依赖
                "conda run -n testbed bash -c 'unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && pip install pytest-json-report pytest-cov'",
                # 在 testbed 环境中运行脚本
                "conda run -n testbed bash -c 'cd /host_scripts && python trace.py --project-root /testbed --max-workers 16 --output-dir /workspace/result'"
            ]
            
            for i, cmd in enumerate(commands, 1):
                self.logger.info(f"执行命令 {i}/{len(commands)}: {cmd[:80]}...")
                
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
                    
                    # 记录输出
                    if stdout_output:
                        self.logger.debug(f"STDOUT:\n{stdout_output}")
                    if stderr_output:
                        self.logger.debug(f"STDERR:\n{stderr_output}")
                    
                    if exit_code != 0:
                        self.logger.error(f"命令执行失败 (退出码: {exit_code})")
                        self.logger.error(f"命令: {cmd}")
                        if stderr_output:
                            self.logger.error(f"错误信息:\n{stderr_output}")
                        return False
                    
                    self.logger.info(f"✓ 命令 {i} 执行成功")
                    
                except Exception as e:
                    self.logger.error(f"执行命令时发生异常: {e}")
                    return False
            
            self.logger.info(f"✅ 镜像 {instance_id} 处理完成")
            return True
            
        except docker.errors.ImageNotFound:
            self.logger.error(f"镜像不存在: {image_tag}")
            return False
        except docker.errors.APIError as e:
            self.logger.error(f"Docker API错误: {e}")
            return False
        except Exception as e:
            self.logger.error(f"处理镜像时发生未知错误: {e}", exc_info=True)
            return False
        finally:
            # 清理容器
            if container:
                try:
                    self.logger.info(f"停止并删除容器: {container_name}")
                    container.stop(timeout=10)
                    container.remove()
                    self.logger.debug(f"容器已清理: {container_name}")
                except Exception as e:
                    self.logger.warning(f"清理容器时发生错误: {e}")
    
    def run_batch(self, max_containers: Optional[int] = None):
        """
        批量运行所有目标镜像
        
        Args:
            max_containers: 最大处理容器数量，None表示处理所有
        """
        images = self.get_target_images()
        
        if not images:
            self.logger.warning("没有找到符合条件的镜像")
            return
        
        total = len(images)
        if max_containers:
            total = min(total, max_containers)
            images = images[:max_containers]
        
        self.logger.info(f"\n{'='*60}")
        self.logger.info(f"开始批量处理，共 {total} 个镜像")
        self.logger.info(f"{'='*60}\n")
        
        success_count = 0
        failed_count = 0
        skipped_count = 0
        
        start_time = time.time()
        
        for idx, image_info in enumerate(images, 1):
            self.logger.info(f"\n进度: [{idx}/{total}]")
            
            result = self.run_in_container(image_info)
            
            if result:
                # 检查是否是跳过的
                instance_id = self._extract_instance_identifier(image_info['tag'])
                result_dir = os.path.join(self.result_base_dir, instance_id, "result")
                if self._is_already_processed(result_dir) and idx == 1:
                    skipped_count += 1
                else:
                    success_count += 1
            else:
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
        log_dir=args.log_dir
    )
    
    runner.run_batch(max_containers=args.max)


if __name__ == "__main__":
    main()