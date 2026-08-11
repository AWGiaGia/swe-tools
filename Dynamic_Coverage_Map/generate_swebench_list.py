#!/usr/bin/env python3
"""
从 SWE-bench 数据集生成 Docker 镜像列表
"""

import argparse
import json


def generate_image_list_from_dataset(dataset_name='princeton-nlp/SWE-bench', output_file='swebench_images.txt'):
    """
    从 SWE-bench 数据集生成镜像列表
    
    Args:
        dataset_name: 数据集名称或路径
        output_file: 输出文件路径
    """
    try:
        from datasets import load_dataset
    except ImportError:
        print("错误: 需要安装 datasets 库")
        print("请运行: pip install datasets")
        return
    
    print(f"正在加载数据集: {dataset_name}")
    
    # 加载数据集的所有 split
    try:
        dataset = load_dataset(dataset_name)
        print(f"数据集包含的 splits: {list(dataset.keys())}")
        
        # 合并所有 split
        all_items = []
        for split_name, split_data in dataset.items():
            print(f"  - {split_name}: {len(split_data)} 个实例")
            all_items.extend(split_data)
        
        dataset = all_items
        
    except Exception as e:
        # 如果加载所有 split 失败，尝试只加载 test split
        print(f"无法加载所有 splits: {e}")
        print("尝试只加载 test split...")
        dataset = load_dataset(dataset_name, split='test')
        dataset = list(dataset)
    
    print(f"\n总共: {len(dataset)} 个实例")
    
    # 提取实例ID并生成镜像名称
    image_tags = []
    
    for item in dataset:
        instance_id = item['instance_id']
        # SWE-bench 镜像格式: sweb.eval.x86_64.{instance_id}
        # 保持原始的 __ 格式，不要替换！
        image_tag = f"sweb.eval.x86_64.{instance_id}"
        image_tags.append(image_tag)
    
    # 保存到文件
    with open(output_file, 'w') as f:
        f.write(f"# SWE-bench Docker 镜像列表\n")
        f.write(f"# 数据集: {dataset_name}\n")
        f.write(f"# 总数: {len(image_tags)}\n")
        f.write(f"# 生成时间: {__import__('datetime').datetime.now().isoformat()}\n\n")
        
        for tag in sorted(image_tags):
            f.write(f"{tag}\n")
    
    print(f"\n成功生成镜像列表: {output_file}")
    print(f"总共 {len(image_tags)} 个镜像")
    
    # 显示统计信息
    repos = {}
    for tag in image_tags:
        # 格式: sweb.eval.x86_64.{repo}_{repo}-{issue}
        # 去掉前缀后按第一个 _ 分割
        if tag.startswith("sweb.eval.x86_64."):
            remainder = tag[len("sweb.eval.x86_64."):]
            # 提取仓库名（第一个下划线之前的部分）
            parts = remainder.split('_', 1)
            if len(parts) > 0:
                repo = parts[0]
                repos[repo] = repos.get(repo, 0) + 1
    
    if repos:
        print("\n按仓库统计:")
        for repo, count in sorted(repos.items(), key=lambda x: x[1], reverse=True)[:10]:
            print(f"  {repo}: {count}")


def main():
    parser = argparse.ArgumentParser(description='从 SWE-bench 数据集生成 Docker 镜像列表')
    
    parser.add_argument(
        '--dataset',
        default='princeton-nlp/SWE-bench_Lite',
        help='数据集名称或本地路径 (默认: princeton-nlp/SWE-bench_Lite)'
    )
    
    parser.add_argument(
        '--output',
        default='swebench_lite_images.txt',
        help='输出文件路径 (默认: ./swebench_lite_images.txt)'
    )
    
    args = parser.parse_args()
    
    generate_image_list_from_dataset(args.dataset, args.output)


if __name__ == '__main__':
    main()
