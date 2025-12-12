# utils/trace.py
from typing import List, Dict, Optional, Union
from pathlib import Path
from tempfile import TemporaryDirectory
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import partial

import os
import sys
import argparse
import shutil
import subprocess
import json
import random as rnd
import re

printf = partial(print, flush=True)


pytest_ignore_list = [
    "doc/",
    "examples/", 
    "benchmarks/",
    "build/",
    "sklearn/externals/",
    "build_tools/",
    "*/build_tools/*",  # 添加通配符模式
    "build_tools/circle/list_versions.py"  # 直接忽略具体文件
]



def detect_django_settings(project_root: str) -> Optional[str]:
    """
    检测Django项目并返回合适的settings模块路径
    
    Args:
        project_root: 项目根目录
        
    Returns:
        settings模块路径，如果不是Django项目则返回None
    """
    project_path = Path(project_root).resolve()
    
    printf(f"[Django检测] 开始检测项目: {project_root}")
    
# ===== 0. 特殊处理：检测是否为 Django 源码仓库本身 =====
    is_django_source_repo = (
        (project_path / "tests" / "runtests.py").exists() and
        (project_path / "django" / "__init__.py").exists()
    )
    
    if is_django_source_repo:
        printf(f"[Django检测] ✅ 检测到 Django 源码仓库")
        
        # 优先尝试使用官方测试设置文件
        official_settings_candidates = [
            (project_path / "tests" / "test_sqlite.py", "test_sqlite"),
            (project_path / "tests" / "test_settings.py", "test_settings"),
            (project_path / "tests" / "settings.py", "settings"),
        ]
        
        for settings_file, settings_module in official_settings_candidates:
            if settings_file.exists():
                printf(f"[Django检测] ✅ 找到官方测试设置: {settings_file} -> {settings_module}")
                return settings_module
        
        # 官方设置不存在时，创建兼容的临时设置
        printf(f"[Django检测] ⚠️ 未找到官方测试设置，创建临时 settings")
        temp_settings_path = project_path / "_trace_test_settings.py"
        temp_settings_content = '''\
# Auto-generated settings for Django source repo testing
# This is a fallback when official test settings are not found
import os
import warnings

# 基础安全配置
SECRET_KEY = 'django-insecure-test-key-for-trace-collection-only'

# 数据库配置 - 与官方测试一致
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    },
    'other': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    },
}

# 核心应用
INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'django.contrib.sites',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.admin.apps.SimpleAdminConfig',
    'django.contrib.staticfiles',
    'django.contrib.redirects',
    'django.contrib.flatpages',
    'django.contrib.sitemaps',
    'django.contrib.syndication',
    'django.contrib.humanize',
]

# 尝试添加 postgres 支持（如果可用）
try:
    import psycopg2
    INSTALLED_APPS.append('django.contrib.postgres')
except ImportError:
    pass

SITE_ID = 1
USE_TZ = False  # 与大多数 Django 测试默认行为一致
USE_I18N = True
LANGUAGE_CODE = 'en-us'
DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'

# 加快密码哈希以加速测试
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

# 模板配置
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

# 中间件
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
]

# 静态文件配置
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(os.path.dirname(__file__), 'static_collected')
STATICFILES_FINDERS = [
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
]

# URL 配置 - 使用空的 urlpatterns
ROOT_URLCONF = ''

# 日志配置 - 提供基本的 handler 避免 IndexError
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'WARNING',
    },
    'loggers': {
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}

DEBUG = True
ALLOWED_HOSTS = ['*']

# 警告过滤 - 将弃用警告转为错误（与官方测试行为一致）
warnings.filterwarnings('error', category=DeprecationWarning)
warnings.filterwarnings('error', category=PendingDeprecationWarning)
'''
        try:
            with open(temp_settings_path, 'w', encoding='utf-8') as f:
                f.write(temp_settings_content)
            printf(f"[Django检测] ✅ 创建临时 settings 文件: {temp_settings_path}")
            return "_trace_test_settings"
        except Exception as e:
            printf(f"[Django检测] ❌ 创建临时 settings 失败: {e}")
            # 继续尝试其他方法
    
    # ===== 1. 基础检测：检查是否为Django项目 =====
    has_manage_py = (project_path / "manage.py").exists()
    printf(f"[Django检测] 根目录manage.py存在: {has_manage_py}")
    
    # 检查子目录中的manage.py
    has_manage_py_subdir = False
    if not has_manage_py:
        for subdir in ["tests", "test", "django_tests"]:
            if (project_path / subdir / "manage.py").exists():
                has_manage_py_subdir = True
                printf(f"[Django检测] 子目录{subdir}/manage.py存在: True")
                break
    
    # 检查Django特征目录
    django_feature_dirs = ["tests", "django", "conf"]
    has_django_dirs = False
    try:
        dirs = [d for d in os.listdir(project_path) 
                if (project_path / d).is_dir() and not d.startswith('.')]
        printf(f"[Django检测] 扫描到 {len(dirs)} 个非隐藏目录")
        
        # 检查是否有Django特征目录
        for feature_dir in django_feature_dirs:
            if feature_dir in dirs:
                has_django_dirs = True
                printf(f"[Django检测] 发现Django特征目录: {feature_dir}")
                break
                
        has_settings_in_subdir = any((project_path / d / "settings.py").exists() for d in dirs)
        printf(f"[Django检测] settings.py存在于子目录: {has_settings_in_subdir}")
    except Exception as e:
        printf(f"[Django检测] 扫描目录时出错: {e}")
        has_settings_in_subdir = False
    
    # 尝试导入Django模块来确认
    is_django_importable = False
    try:
        import django
        is_django_importable = True
        printf(f"[Django检测] Django模块可导入: True (版本: {django.VERSION})")
    except ImportError:
        printf(f"[Django检测] Django模块不可导入")
    
    # 综合判断是否为Django项目
    is_django_project = (has_manage_py or has_manage_py_subdir or 
                         has_settings_in_subdir or has_django_dirs or 
                         is_django_importable)
    
    if not is_django_project:
        printf(f"[Django检测] ❌ 非Django项目，跳过Django配置")
        return None
    
    printf(f"[Django检测] ✅ 检测到Django项目")
    
    # ===== 2. 尝试从manage.py中提取settings配置 =====
    manage_py_paths = [
        project_path / "manage.py",
        project_path / "tests" / "manage.py",
        project_path / "test" / "manage.py",
    ]
    
    for manage_py_path in manage_py_paths:
        if manage_py_path.exists():
            printf(f"[Django检测] 尝试从{manage_py_path}中提取settings配置...")
            try:
                with open(manage_py_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # 查找 DJANGO_SETTINGS_MODULE 设置
                    match = re.search(r'DJANGO_SETTINGS_MODULE["\']?\s*,?\s*["\']([^"\']+)["\']', content)
                    if match:
                        settings_module = match.group(1)
                        printf(f"[Django检测] ✅ 从{manage_py_path.name}中找到: {settings_module}")
                        return settings_module
                    else:
                        printf(f"[Django检测] ⚠️  {manage_py_path.name}中未找到DJANGO_SETTINGS_MODULE")
            except Exception as e:
                printf(f"[Django检测] ❌ 解析{manage_py_path.name}失败: {e}")
    
    # ===== 3. 递归搜索settings文件并推断模块路径 =====
    printf(f"[Django检测] 开始递归搜索settings文件...")
    settings_files = []
    
    # 限制搜索深度，避免扫描过多文件
    max_depth = 3
    for root, dirs, files in os.walk(project_path):
        # 计算当前深度
        depth = root[len(str(project_path)):].count(os.sep)
        if depth >= max_depth:
            dirs[:] = []  # 不再深入
            continue
        
        # 跳过常见的非代码目录
        dirs[:] = [d for d in dirs if d not in ['.git', '__pycache__', 'node_modules', 
                                                   '.pytest_cache', 'build', 'dist', '.tox']]
        
        for file in files:
            if file in ['settings.py', 'test_settings.py']:
                full_path = Path(root) / file
                try:
                    # 计算相对路径并转换为模块路径
                    rel_path = full_path.relative_to(project_path)
                    module_path = str(rel_path.with_suffix('')).replace(os.sep, '.')
                    settings_files.append((full_path, module_path))
                    printf(f"[Django检测]   找到: {rel_path} -> {module_path}")
                except ValueError:
                    continue
    
    printf(f"[Django检测] 共找到 {len(settings_files)} 个settings文件")
    
    # ===== 4. 扩展的可能settings路径列表 =====
    possible_settings = [
        # 从搜索结果中提取的路径
        *[module_path for _, module_path in settings_files],
        # 常见的默认路径
        "tests.settings",
        "test.settings",
        "tests.test_settings",
        "django_tests.settings",
        "test_settings",
        "django.conf.settings",
        "settings",
        "conf.settings",
        "config.settings",
        # SWE-bench特有路径
        "tests.settings_tests",
    ]
    
    # 去重，保持顺序
    seen = set()
    unique_settings = []
    for s in possible_settings:
        if s not in seen:
            seen.add(s)
            unique_settings.append(s)
    
    printf(f"[Django检测] 将尝试 {len(unique_settings)} 个可能的settings路径")
    
    # ===== 5. 验证settings文件是否实际存在 =====
    for settings_path in unique_settings:
        settings_file_path = settings_path.replace('.', os.sep) + '.py'
        full_path = project_path / settings_file_path
        
        if full_path.exists():
            printf(f"[Django检测] ✅ 验证通过，找到settings文件: {settings_path}")
            return settings_path
    
    # ===== 6. 兜底策略：返回最可能的默认值 =====
    printf(f"[Django检测] ⚠️  未找到确切的settings文件")
    
    # 根据项目特征选择兜底值
    if has_django_dirs or has_settings_in_subdir:
        fallback = "tests.settings"
        printf(f"[Django检测] 使用兜底策略: {fallback} (检测到Django特征)")
    else:
        fallback = "test_settings"
        printf(f"[Django检测] 使用兜底策略: {fallback}")
    
    return fallback


def run_subprocess_compatible(cmd, shell=True, cwd=None, env=None):
    """兼容Python 3.6和3.7+的subprocess调用"""
    if sys.version_info >= (3, 7):
        # Python 3.7+ 支持 capture_output
        return subprocess.run(cmd, shell=shell, cwd=cwd, env=env, capture_output=True)
    else:
        # Python 3.6 使用传统方式
        return subprocess.run(cmd, shell=shell, cwd=cwd, env=env, 
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def parse_args():

    def int_or_none(value):
        if value in ["None", "none", "NONE", "null", "NULL", "Null", "NULL"]:
            return None
        return int(value)

    def true_or_false(value):
        if value in ["True", "true", "TRUE"]:
            return True
        return False

    parser = argparse.ArgumentParser(description='Generate pytest trace for a Python project')
    parser.add_argument('--project-root', type=str, help='Path to the Python project.')
    parser.add_argument('--max-workers', type=int_or_none, default=None, help='Number of workers to use for the trace.')
    parser.add_argument('--max-tests', type=int_or_none, default=None, help='Maximum number of tests to trace.')
    parser.add_argument('--random', type=true_or_false, default=False, help='Whether to randomize the order of tests.')
    parser.add_argument('--random-seed', type=int, default=42, help='The random seed to use for the trace.')
    parser.add_argument('--output-dir', type=str, help='Output directory to save the trace data.')

    return parser.parse_args()


def clear_python_cache(dir: str) -> None:
    """
    Clear the Python cache in the project.
    """
    print("clearing python cache...")
    for root, dirs, files in os.walk(dir):
        for dir in dirs:
            if dir == "__pycache__" or dir == ".pytest_cache":
                shutil.rmtree(os.path.join(root, dir))


def run_pytest(cwd: str, pytest_args: List[str], raise_on_error: bool = True) -> Optional[str]:
    """
    Run pytest with the given arguments.
    
    Args:
        cwd: 工作目录
        pytest_args: pytest参数列表
        raise_on_error: 如果为False，失败时返回错误信息而不抛异常
        
    Returns:
        如果失败且raise_on_error=False，返回错误信息；否则返回None
    """
    printf(f"\n[run_pytest] 开始执行pytest")
    printf(f"[run_pytest] 工作目录: {cwd}")
    
    pytest_args_str = " ".join(pytest_args)
    # create pytest command
    cmd = f"{sys.executable} -m pytest {pytest_args_str}"
    printf(f"[run_pytest] 命令: {cmd[:200]}...")  # 只打印前200个字符

    # set environment variable
    env = os.environ.copy()
    # 清除无效的代理配置，避免 LocationParseError
    for proxy_var in ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 
                    'ftp_proxy', 'FTP_PROXY', 'no_proxy', 'NO_PROXY']:
        env.pop(proxy_var, None)
    
    # 构建 PYTHONPATH
    pythonpath_parts = [f"{cwd}/src", str(cwd)]
    
    # 仅对 Django 源码仓库添加 tests 目录到 PYTHONPATH
    is_django_source = (
        (Path(cwd) / "tests" / "runtests.py").exists() and
        (Path(cwd) / "django" / "__init__.py").exists()
    )
    if is_django_source and (Path(cwd) / "tests").is_dir():
        pythonpath_parts.insert(0, f"{cwd}/tests")
        printf(f"[run_pytest] 检测到 Django 源码仓库，添加 tests 到 PYTHONPATH")
    
    existing_pythonpath = env.get('PYTHONPATH', '')
    if existing_pythonpath:
        pythonpath_parts.append(existing_pythonpath)
    env["PYTHONPATH"] = ":".join(pythonpath_parts)
    env["SETUPTOOLS_USE_DISTUTILS"] = "local"
    printf(f"[run_pytest] PYTHONPATH: {env['PYTHONPATH']}")
    
    # Django项目特殊处理
    django_settings = detect_django_settings(cwd)
    if django_settings:
        env["DJANGO_SETTINGS_MODULE"] = django_settings
        printf(f"[run_pytest] ✅ 设置DJANGO_SETTINGS_MODULE={django_settings}")
    else:
        printf(f"[run_pytest] 非Django项目，不设置DJANGO_SETTINGS_MODULE")

    # run pytest
    printf(f"[run_pytest] 执行pytest...")
    result = run_subprocess_compatible(cmd, shell=True, cwd=cwd, env=env)
    printf(f"[run_pytest] pytest执行完成，返回码: {result.returncode}")

    # clear python cache
    clear_python_cache(cwd)

    if result.returncode != 0:
        stdout_text = result.stdout.decode()
        stderr_text = result.stderr.decode()
        
        printf(f"[run_pytest] ⚠️  pytest返回非零退出码: {result.returncode}")
        
        # 返回码2: 部分测试收集失败，但可以继续
        if result.returncode == 2:
            printf(f"[run_pytest] 返回码2: 部分测试收集失败，但可以继续")
            printf(f"⚠️  pytest collection completed with some errors (return code {result.returncode})")
            
            # === 新增：输出完整的 stdout 和 stderr ===
            printf(f"\n[run_pytest] ========== 完整 STDOUT（前3000字符）==========")
            printf(stdout_text[:3000])
            if len(stdout_text) > 3000:
                printf(f"\n... (总长度: {len(stdout_text)} 字符，已截断)")
            printf(f"\n[run_pytest] ========== 完整 STDERR（前3000字符）==========")
            printf(stderr_text[:3000])
            if len(stderr_text) > 3000:
                printf(f"\n... (总长度: {len(stderr_text)} 字符，已截断)")
            printf(f"[run_pytest] ============================================\n")
            
            # === 改进：支持更多错误格式的提取 ===
            error_patterns = [
                r'^ERROR .*',           # 标准 ERROR 行
                r'^ERROR collecting .*', # 收集错误
                r'^INTERNALERROR.*',    # 内部错误
                r'.*ImportError.*',     # 导入错误
                r'.*ModuleNotFoundError.*',  # 模块未找到
                r'^E\s+.*',             # pytest 的 E 开头错误
            ]
            
            all_error_lines = []
            for pattern in error_patterns:
                matches = re.findall(pattern, stdout_text, re.MULTILINE)
                all_error_lines.extend(matches)
            
            # 去重
            all_error_lines = list(dict.fromkeys(all_error_lines))
            
            if all_error_lines:
                printf(f"[run_pytest] 检测到 {len(all_error_lines)} 个错误行")
                printf(f"📋 Collection errors found:")
                for i, error_line in enumerate(all_error_lines[:15], 1):
                    printf(f"  {i}. {error_line[:150]}")  # 每行最多显示150字符
                if len(all_error_lines) > 15:
                    printf(f"  ... 和其他 {len(all_error_lines) - 15} 个错误")
            else:
                printf(f"[run_pytest] ⚠️  未检测到明确的错误行")
            
            # === 新增：尝试从 JSON 报告中提取错误信息 ===
            try:
                report_path = None
                # 从 pytest_args 中查找 json-report-file 参数
                for i, arg in enumerate(pytest_args):
                    if arg == '--json-report-file' and i + 1 < len(pytest_args):
                        report_path = pytest_args[i + 1]
                        break
                
                if report_path and os.path.exists(report_path):
                    printf(f"[run_pytest] 尝试从 JSON 报告读取错误信息: {report_path}")
                    with open(report_path, 'r', encoding='utf-8') as f:
                        report_data = json.load(f)
                    
                    # 检查 collectors 中的错误
                    if 'collectors' in report_data:
                        collection_errors = []
                        for collector in report_data['collectors']:
                            if 'longrepr' in collector and collector['longrepr']:
                                collection_errors.append({
                                    'nodeid': collector.get('nodeid', 'unknown'),
                                    'error': collector['longrepr'][:500]  # 限制长度
                                })
                        
                        if collection_errors:
                            printf(f"[run_pytest] JSON报告中发现 {len(collection_errors)} 个收集错误:")
                            for i, err in enumerate(collection_errors[:5], 1):
                                printf(f"  错误 {i}: {err['nodeid']}")
                                printf(f"    {err['error'][:200]}...")
                            if len(collection_errors) > 5:
                                printf(f"  ... 和其他 {len(collection_errors) - 5} 个错误")
                    
                    # 检查 summary 中的错误统计
                    if 'summary' in report_data:
                        summary = report_data['summary']
                        printf(f"[run_pytest] 测试摘要: {summary}")
                else:
                    printf(f"[run_pytest] JSON报告不存在或路径未找到")
            except Exception as e:
                printf(f"[run_pytest] ⚠️  解析 JSON 报告失败: {e}")
            
            # === 新增：保存完整输出到诊断文件 ===
            try:
                # 获取输出目录
                output_dir = None
                for i, arg in enumerate(pytest_args):
                    if arg.startswith('--json-report-file='):
                        output_dir = os.path.dirname(arg.split('=')[1])
                        break
                    elif arg == '--json-report-file' and i + 1 < len(pytest_args):
                        output_dir = os.path.dirname(pytest_args[i + 1])
                        break
                
                if output_dir:
                    diagnostic_file = os.path.join(output_dir, "pytest_collection_diagnostic.log")
                    with open(diagnostic_file, 'w', encoding='utf-8') as f:
                        f.write("=" * 80 + "\n")
                        f.write("PYTEST COLLECTION DIAGNOSTIC LOG\n")
                        f.write("=" * 80 + "\n\n")
                        f.write(f"Return Code: {result.returncode}\n")
                        f.write(f"Working Directory: {cwd}\n")
                        f.write(f"Command: {sys.executable} -m pytest {' '.join(pytest_args[:5])}...\n\n")
                        f.write("=" * 80 + "\n")
                        f.write("COMPLETE STDOUT:\n")
                        f.write("=" * 80 + "\n")
                        f.write(stdout_text)
                        f.write("\n\n" + "=" * 80 + "\n")
                        f.write("COMPLETE STDERR:\n")
                        f.write("=" * 80 + "\n")
                        f.write(stderr_text)
                        f.write("\n\n" + "=" * 80 + "\n")
                        f.write("END OF DIAGNOSTIC LOG\n")
                        f.write("=" * 80 + "\n")
                    printf(f"[run_pytest] ✅ 完整诊断日志已保存: {diagnostic_file}")
            except Exception as e:
                printf(f"[run_pytest] ⚠️  保存诊断日志失败: {e}")
            
            # 提取成功收集的测试数量
            match = re.search(r'(\d+) tests? collected', stdout_text)
            if match:
                collected_count = match.group(1)
                printf(f"[run_pytest] 成功收集的测试数: {collected_count}")
                printf(f"✅ Successfully collected {collected_count} tests despite collection errors")
            else:
                printf(f"[run_pytest] ⚠️  无法从输出中提取收集的测试数量")
                printf(f"[run_pytest] 这可能表示测试收集完全失败")
                # 返回错误信息供调用者判断
                if not raise_on_error:
                    return stdout_text + "\n" + stderr_text
            
            printf(f"ℹ️  Continuing with successfully collected tests...")
            printf("─" * 60)
            
        # 返回码1或其他: 真正的失败
        else:
            printf(f"[run_pytest] ❌ pytest执行失败，返回码: {result.returncode}")
            printf(f"❌ pytest failed with return code {result.returncode}")
            printf(f"[run_pytest] STDOUT (前500字符):\n{stdout_text[:500]}")
            printf(f"[run_pytest] STDERR (前500字符):\n{stderr_text[:500]}")
            printf(f"pytest output:\n{stdout_text}")
            printf(f"pytest error:\n{stderr_text}")
            
            if raise_on_error:
                raise Exception(f"pytest failed with return code {result.returncode}")
            else:
                return stdout_text + "\n" + stderr_text
    else:
        printf(f"[run_pytest] ✅ pytest执行成功")
        return None
def collect_tests(
    project_root: str,
    output_dir: str,
    random: bool = False,
    random_seed: int = 42,
    max_tests: Optional[int] = None,
    report_file: str = "tests-info.json",
) -> List[str]:
    """
    Collect the tests in the project.
    """

    # set current working directory
    cwd = Path(project_root).resolve()
    printf(f"collecting tests in {cwd}")
    printf(f"current working directory:\n{cwd}")

    _output_dir = Path(output_dir).resolve()
    # create output directory if it doesn't exist
    os.makedirs(_output_dir, exist_ok=True)

    # create pytest args
    pytest_args = [
        "--collect-only",
        "--cache-clear",
        f"--rootdir={cwd}",
        "-o",
        f"cache_dir={cwd}/.pytest_cache",
        "--json-report",
        "--json-report-indent=4",
        f"--json-report-file={_output_dir}/{report_file}",
        "--tb=short",  # 改为显示简短的traceback（原来是 --tb=no）
        "-v"           # 改为详细模式（原来是 -q）
    ]

    # 添加忽略参数
    for ignore_dir in pytest_ignore_list:
        pytest_args.append(f"--ignore={ignore_dir}")

    # run pytest
    # print("cwd = {}".format(cwd))
    # print("pytest_args = {}".format(pytest_args))

    # 第一次尝试：使用标准配置收集测试
    printf(f"[collect_tests] 第一次尝试：使用标准配置收集测试")
    error_info = run_pytest(cwd=cwd, pytest_args=pytest_args, raise_on_error=False)
    
    # 检查是否因为 pytest-doctestplus 失败
    if error_info and "pytest_doctestplus" in error_info.lower():
        printf(f"\n[collect_tests] ⚠️  检测到 pytest-doctestplus 插件导致收集失败")
        printf(f"[collect_tests] 尝试禁用 pytest-doctestplus 插件后重试...")
        
        # 添加禁用插件的参数
        retry_args = pytest_args.copy()
        retry_args.extend(["-p", "no:doctestplus"])
        
        # 第二次尝试
        printf(f"[collect_tests] 第二次尝试：禁用 doctestplus 插件")
        error_info_retry = run_pytest(cwd=cwd, pytest_args=retry_args, raise_on_error=False)
        
        if error_info_retry:
            # 还是失败，记录并继续
            printf(f"[collect_tests] ❌ 禁用插件后仍然失败")
            printf(f"[collect_tests] 将尝试使用已收集的测试继续...")
        else:
            printf(f"[collect_tests] ✅ 禁用插件后收集成功！")
            # 创建标记文件记录使用了降级方案
            try:
                marker_file = Path(_output_dir) / "doctestplus_disabled.marker"
                with open(marker_file, 'w') as f:
                    f.write("pytest-doctestplus plugin was disabled due to compatibility issues\n")
                printf(f"[collect_tests] 已创建标记文件: {marker_file}")
            except Exception as e:
                printf(f"[collect_tests] ⚠️  创建标记文件失败: {e}")

    # 读取收集结果
    report_path = Path(_output_dir) / report_file
    if not report_path.exists():
        printf(f"[collect_tests] ⚠️  JSON报告不存在: {report_path}")
        printf(f"[collect_tests] 可能没有收集到任何测试")
        return []
    
    report = json.load(open(report_path))
    collectors = report['collectors']
    tests = []
    for collector in collectors:
        for res in collector['result']:
            if res['type'] not in ['Function', 'TestCaseFunction']:
                continue
            tests.append(res['nodeid'])
    printf(f"collected {len(tests)} test items")

    printf(f"merging tests with same function name...")
    tests = list(set([re.sub(r"\[.*?\]", "", test) for test in tests]))
    printf(f"total {len(tests)} merged tests")

    if random:
        printf(f"random enabled, shuffling tests with random seed {random_seed}")
        rnd.seed(random_seed)
        rnd.shuffle(tests)

    if max_tests:
        printf(f"selecting {max_tests} tests as --max-tests is set to {max_tests}")
        tests = tests[:max_tests]

    return tests


# def run_trace_test(cwd: str, pytest_args: List[str], trace_file: str, timeout: int = 120) -> None:
#     """
#     Run hooked pytest with the given arguments.
#     """
#     trace_args = [
#         "--trace-output",
#         f"{trace_file}",
#         "--program",
#         "pytest",
#     ] + pytest_args
#     trace_args_str = " ".join(trace_args)
#     cmd = f"sweflow-hooks-python {trace_args_str}"

#     # set environment variable
#     env = os.environ.copy()
#     env["PYTHONPATH"] = f"{cwd}/src:{env.get('PYTHONPATH', '')}"
#     env["SETUPTOOLS_USE_DISTUTILS"] = "local"

#     # run pytest
#     result = subprocess.run(cmd, shell=True, cwd=cwd, env=env, capture_output=True, timeout=timeout)
#     if result.returncode != 0:
#         printf(f"pytest failed with return code {result.returncode}")
#         printf(f"pytest output:\n{result.stdout.decode()}")
#         printf(f"pytest error:\n{result.stderr.decode()}")
#         raise Exception(f"pytest failed with return code {result.returncode}")


# def run_trace_test(cwd: str, pytest_args: List[str], trace_file: str, timeout: int = 120) -> None:
#     """
#     Run hooked pytest with the given arguments.
#     """
#     trace_args = [
#         "--trace-output",
#         f"{trace_file}",
#         "--program",
#         "pytest",
#     ] + pytest_args
#     trace_args_str = " ".join(trace_args)
    
#     # 直接使用Python模块运行，不依赖命令行工具安装
#     cmd = f"{sys.executable} -m sweflow_trace.python.hooks {trace_args_str}"

#     # set environment variable
#     env = os.environ.copy()
#     env["PYTHONPATH"] = f"{cwd}/src:{env.get('PYTHONPATH', '')}"
#     env["SETUPTOOLS_USE_DISTUTILS"] = "local"

#     # run pytest
#     result = subprocess.run(cmd, shell=True, cwd=cwd, env=env, capture_output=True, timeout=timeout)
#     if result.returncode != 0:
#         printf(f"pytest failed with return code {result.returncode}")
#         printf(f"pytest output:\n{result.stdout.decode()}")
#         printf(f"pytest error:\n{result.stderr.decode()}")
#         raise Exception(f"pytest failed with return code {result.returncode}")


def run_trace_test(cwd: str, pytest_args: List[str], trace_file: str, timeout: int = 120) -> None:
    """
    Run hooked pytest with the given arguments.
    """
    printf(f"\n[run_trace_test] 开始执行带跟踪的pytest")
    printf(f"[run_trace_test] 工作目录: {cwd}")
    printf(f"[run_trace_test] 跟踪文件: {trace_file}")
    printf(f"[run_trace_test] 超时时间: {timeout}秒")
    
    trace_args = [
        "--trace-output",
        f"{trace_file}",
        "--program",
        "pytest",
    ] + pytest_args
    
    # 容器内运行
    hooks_file = Path("/host_scripts/hooks.py")
    printf(f"[run_trace_test] hooks文件路径: {hooks_file}")
    
    if not hooks_file.exists():
        printf(f"[run_trace_test] ❌ hooks.py不存在: {hooks_file}")
        raise FileNotFoundError(f"hooks.py not found at {hooks_file}")
    else:
        printf(f"[run_trace_test] ✅ hooks.py存在")
    
    # 使用args列表而不是shell命令，避免特殊字符问题
    cmd_args = [sys.executable, str(hooks_file)] + trace_args
    printf(f"[run_trace_test] 命令参数 (前3个): {cmd_args[:3]}")
    printf(f"[run_trace_test] pytest参数数量: {len(pytest_args)}")

    # set environment variable
    env = os.environ.copy()
    # 清除无效的代理配置，避免 LocationParseError
    for proxy_var in ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY',
                    'ftp_proxy', 'FTP_PROXY', 'no_proxy', 'NO_PROXY']:
        env.pop(proxy_var, None)
    
    # 构建 PYTHONPATH
    pythonpath_parts = [f"{cwd}/src", str(cwd)]
    
    # 仅对 Django 源码仓库添加 tests 目录到 PYTHONPATH
    is_django_source = (
        (Path(cwd) / "tests" / "runtests.py").exists() and
        (Path(cwd) / "django" / "__init__.py").exists()
    )
    if is_django_source and (Path(cwd) / "tests").is_dir():
        pythonpath_parts.insert(0, f"{cwd}/tests")
    
    existing_pythonpath = env.get('PYTHONPATH', '')
    if existing_pythonpath:
        pythonpath_parts.append(existing_pythonpath)
    env["PYTHONPATH"] = ":".join(pythonpath_parts)
    env["SETUPTOOLS_USE_DISTUTILS"] = "local"
    
    # Django项目特殊处理
    django_settings = detect_django_settings(cwd)
    if django_settings:
        env["DJANGO_SETTINGS_MODULE"] = django_settings
        printf(f"[run_trace_test] ✅ 设置DJANGO_SETTINGS_MODULE={django_settings}")
    else:
        printf(f"[run_trace_test] 非Django项目，不设置DJANGO_SETTINGS_MODULE")

    # 不使用shell=True，直接传递参数列表
    printf(f"[run_trace_test] 开始执行subprocess...")
    try:
        result = subprocess.run(cmd_args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        printf(f"[run_trace_test] subprocess执行完成，返回码: {result.returncode}")
    except subprocess.TimeoutExpired:
        printf(f"[run_trace_test] ❌ 执行超时 ({timeout}秒)")
        raise
    except Exception as e:
        printf(f"[run_trace_test] ❌ 执行异常: {e}")
        raise
    
    if result.returncode != 0:
            stdout_text = result.stdout.decode()
            stderr_text = result.stderr.decode()
            
            printf(f"[run_trace_test] ❌ pytest失败，返回码: {result.returncode}")
            
            # 完整输出stdout和stderr（不截断）
            printf(f"[run_trace_test] ========== 完整STDOUT ==========")
            printf(stdout_text)
            printf(f"[run_trace_test] ========== 完整STDERR ==========")
            printf(stderr_text)
            printf(f"[run_trace_test] ================================")
            
            # 分析错误类型
            error_type = "UNKNOWN"
            if "ImproperlyConfigured" in stdout_text or "ImproperlyConfigured" in stderr_text:
                error_type = "DJANGO_CONFIG_ERROR"
            elif "ImportError" in stdout_text or "ModuleNotFoundError" in stdout_text:
                error_type = "IMPORT_ERROR"
            elif "INTERNALERROR" in stdout_text:
                error_type = "PYTEST_INTERNAL_ERROR"
            elif "ERROR" in stdout_text and "collection" in stdout_text.lower():
                error_type = "COLLECTION_ERROR"
            elif "AttributeError" in stdout_text or "AttributeError" in stderr_text:
                error_type = "ATTRIBUTE_ERROR"
            
            printf(f"[run_trace_test] 错误类型: {error_type}")
            
            printf(f"pytest failed with return code {result.returncode}")
            printf(f"pytest output:\n{stdout_text}")
            printf(f"pytest error:\n{stderr_text}")
            raise Exception(f"pytest failed with return code {result.returncode}")


def get_test_func_id(test_result: Dict) -> str:
    """
    Get the test function id from the test result.
    """
    func_node = re.sub(r"\[.*?\]", "", test_result['nodeid'])
    filepath = func_node.split("::")[0]
    func_name = func_node.split("::")[-1]
    lineno = test_result['lineno'] + 1  # convert 0-based to 1-based
    return f"{filepath}:{lineno}:{func_name}"


def trace_test(test: str, cwd: str, temp_dir: str) -> None:
    printf(f"\n[trace_test] ==================== 开始跟踪测试 ====================")
    printf(f"[trace_test] 测试: {test}")
    printf(f"[trace_test] 工作目录: {cwd}")
    
    try:
        with TemporaryDirectory(dir=temp_dir) as _temp_dir:
            printf(f"[trace_test] 临时目录: {_temp_dir}")

            # pytest_args = [
            #     "--cache-clear",
            #     f"--rootdir={cwd}",
            #     "-o",
            #     f"cache_dir={_temp_dir}/.pytest_cache",
            #     "--no-cov",
            #     "--json-report",
            #     "--json-report-indent=4",
            #     f"--json-report-file={_temp_dir}/report.json",
            #     "--tb=short",  # 修改：显示简短的traceback（从 --tb=no 改为 --tb=short）
            #     "-v",          # 修改：详细模式（从 -q 改为 -v）
            #     "-s",          # 新增：显示print输出
            #     test,
            # ]

            pytest_args = [
                "--cache-clear",
                f"--rootdir={cwd}",
                "-o",
                f"cache_dir={_temp_dir}/.pytest_cache",
                "--no-cov",
                "--json-report",
                "--json-report-indent=4",
                f"--json-report-file={_temp_dir}/report.json",
                "--tb=long",   # 完整的traceback
                "-vv",         # 非常详细的模式
                "-s",          # 显示print输出
                "--showlocals", # 显示局部变量
                test,
            ]


            # 添加忽略参数
            for ignore_dir in pytest_ignore_list:
                pytest_args.append(f"--ignore={ignore_dir}")

            printf(f"[trace_test] pytest参数数量: {len(pytest_args)}")
            trace_file = f"{_temp_dir}/trace.json"
            printf(f"[trace_test] 跟踪文件路径: {trace_file}")

            run_trace_test(cwd=cwd, pytest_args=pytest_args, trace_file=trace_file)

            printf(f"[trace_test] 读取测试报告...")
            report_file = f"{_temp_dir}/report.json"
            if not os.path.exists(report_file):
                printf(f"[trace_test] ❌ 报告文件不存在: {report_file}")
                return None
            
            test_report = json.load(open(report_file))
            printf(f"[trace_test] 报告中测试数量: {len(test_report.get('tests', []))}")
            
            if not test_report.get('tests'):
                printf(f"[trace_test] ⚠️  报告中没有测试结果")
                return None
                
            test_result = test_report['tests'][0]
            outcome = test_result.get('outcome', 'unknown')
            printf(f"[trace_test] 测试结果: {outcome}")
            
            # passed: 正常执行并通过，收集覆盖信息
            # skipped: pytest 正常跳过（如缺少依赖），不视为失败
            # failed/error: 真正的失败，跳过覆盖收集
            if outcome == 'skipped':
                printf(f"[trace_test] ⏭️  测试被跳过（依赖缺失等）: {test}")
                return {
                    "test-id": test,
                    "test-func-id": get_test_func_id(test_result),
                    "call-relations": [],
                    "status": "skipped"
                }
            
            if outcome != 'passed':
                printf(f"[trace_test] ⚠️  测试未通过，跳过: {test}")
                return None

            printf(f"[trace_test] 读取跟踪文件...")
            if not os.path.exists(trace_file):
                printf(f"[trace_test] ❌ 跟踪文件不存在: {trace_file}")
                return None
                
            call_relationships = json.load(open(trace_file))
            printf(f"[trace_test] 调用关系数量: {len(call_relationships)}")

        test_func_id = get_test_func_id(test_result)
        printf(f"[trace_test] 测试函数ID: {test_func_id}")
        printf(f"[trace_test] ✅ 测试跟踪成功")
        
        return {
            "test-id": test,
            "test-func-id": test_func_id,
            "call-relations": call_relationships,
        }
    except Exception as e:
            printf(f"[trace_test] ❌ 处理测试时出错: {test}")
            printf(f"[trace_test] 错误详情: {type(e).__name__}: {e}")
            import traceback
            full_traceback = traceback.format_exc()
            printf(f"[trace_test] 堆栈跟踪:\n{full_traceback}")
            
            # 保存失败测试的详细信息到独立文件
            try:
                import hashlib
                import time
                
                # 使用测试名hash + 进程ID + 时间戳确保唯一性
                test_hash = hashlib.md5(test.encode()).hexdigest()[:8]
                pid = os.getpid()
                timestamp = int(time.time() * 1000000)  # 微秒级时间戳
                
                # 使用输出目录而不是temp_dir，避免跨instance冲突
                # 如果在容器中，使用 /workspace/result/error_logs
                if os.path.exists('/workspace/result'):
                    error_log_dir = Path('/workspace/result/error_logs')
                elif temp_dir:
                    error_log_dir = Path(temp_dir) / "error_logs"
                else:
                    error_log_dir = Path("/tmp/error_logs")
                
                error_log_dir.mkdir(parents=True, exist_ok=True)
                error_log_file = error_log_dir / f"failed_{test_hash}_pid{pid}_{timestamp}.log"
                
                with open(error_log_file, 'w', encoding='utf-8') as f:
                    f.write(f"Failed Test: {test}\n")
                    f.write(f"Process ID: {pid}\n")
                    f.write(f"Timestamp: {timestamp}\n")
                    f.write(f"Error Type: {type(e).__name__}\n")
                    f.write(f"Error Message: {e}\n")
                    f.write(f"\nFull Traceback:\n{full_traceback}\n")
                    
                    # 如果异常信息中包含pytest输出，也记录下来
                    if hasattr(e, 'args') and len(e.args) > 0:
                        f.write(f"\nException Args:\n{e.args}\n")
                
                printf(f"[trace_test] 错误日志已保存到: {error_log_file}")
            except Exception as log_error:
                printf(f"[trace_test] ⚠️  保存错误日志失败: {log_error}")
            
            return None


def generate_test_traces(
    project_root: str,
    output_dir: str,
    tests: List[str],
    max_workers: Optional[int] = None,
    temp_dir: Optional[str] = None,
) -> None:
    """
    Generate test traces.
    """
    printf(f"\n[generate_test_traces] ========== 开始生成测试跟踪 ==========")
    printf(f"[generate_test_traces] 项目根目录: {project_root}")
    printf(f"[generate_test_traces] 输出目录: {output_dir}")
    printf(f"[generate_test_traces] 测试数量: {len(tests)}")
    printf(f"[generate_test_traces] 最大工作进程: {max_workers}")
    printf(f"[generate_test_traces] 临时目录: {temp_dir}")
    
    cwd = Path(project_root).resolve()
    traces = []
    failed_tests = []
    
    printf(f"[generate_test_traces] 创建进程池，max_workers={max_workers}")
    
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(trace_test, test, cwd, temp_dir): test for test in tests}
        printf(f"[generate_test_traces] 已提交 {len(futures)} 个任务")
        
        skipped_tests = []  # 新增：单独统计跳过的测试
        
        count = 0
        for future in as_completed(futures):
            test_name = futures[future]
            try:
                result = future.result()
                if result:
                    # 区分 skipped 和正常通过的测试
                    if result.get("status") == "skipped":
                        skipped_tests.append(test_name)
                        printf(f"[generate_test_traces] ⏭️  测试跳过: {test_name}")
                    else:
                        traces.append(result)
                        printf(f"[generate_test_traces] ✅ 测试成功: {test_name}")
                else:
                    failed_tests.append(test_name)
                    printf(f"[generate_test_traces] ⚠️  测试失败: {test_name}")
            except Exception as e:
                failed_tests.append(test_name)
                printf(f"[generate_test_traces] ❌ 测试异常: {test_name} - {e}")
            
            count += 1
            if count % 10 == 0 or count == len(tests):
                printf(f"[generate_test_traces] 进度: {count}/{len(tests)} (成功: {len(traces)}, 跳过: {len(skipped_tests)}, 失败: {len(failed_tests)})")

    # clear python cache
        printf(f"[generate_test_traces] 清理Python缓存...")
        clear_python_cache(cwd)

        printf(f"[generate_test_traces] ========== 统计信息 ==========")
        printf(f"[generate_test_traces] 总测试数: {len(tests)}")
        printf(f"[generate_test_traces] 成功生成: {len(traces)}")
        printf(f"[generate_test_traces] 跳过（依赖缺失）: {len(skipped_tests)}")
        printf(f"[generate_test_traces] 失败: {len(failed_tests)}")
        effective_total = len(tests) - len(skipped_tests)
        if effective_total > 0:
            printf(f"[generate_test_traces] 有效成功率: {len(traces)/effective_total*100:.2f}% (排除跳过)")
        printf(f"Generated {len(traces)} traces")
        
        # 保存跳过的测试列表
        if skipped_tests:
            skipped_tests_file = Path(output_dir) / "skipped_tests.txt"
            try:
                with open(skipped_tests_file, 'w', encoding='utf-8') as f:
                    for i, skipped_test in enumerate(skipped_tests, 1):
                        f.write(f"{i}. {skipped_test}\n")
                printf(f"[generate_test_traces] 跳过测试列表已保存到: {skipped_tests_file}")
            except Exception as e:
                printf(f"[generate_test_traces] ⚠️  保存跳过测试列表失败: {e}")
        
        # 生成失败测试报告
        if failed_tests:
            printf(f"\n[generate_test_traces] ========== 失败测试列表 ==========")
            printf(f"[generate_test_traces] 共 {len(failed_tests)} 个失败测试:")
            
            # 保存失败测试列表到文件
            failed_tests_file = Path(output_dir) / "failed_tests.txt"
            try:
                with open(failed_tests_file, 'w', encoding='utf-8') as f:
                    for i, failed_test in enumerate(failed_tests, 1):
                        line = f"{i}. {failed_test}"
                        f.write(line + "\n")
                        if i <= 20:  # 只在控制台显示前20个
                            printf(f"  {line}")
                    
                    if len(failed_tests) > 20:
                        printf(f"  ... 和其他 {len(failed_tests) - 20} 个失败测试")
                
                printf(f"[generate_test_traces] 完整失败列表已保存到: {failed_tests_file}")
            except Exception as e:
                printf(f"[generate_test_traces] ⚠️  保存失败测试列表失败: {e}")

        # 保存测试跟踪
        traces_file = Path(output_dir) / "traces.json"
        try:
            with open(traces_file, 'w', encoding='utf-8') as f:
                json.dump(traces, f, indent=4)
            printf(f"[generate_test_traces] 测试跟踪已保存到: {traces_file}")
        except Exception as e:
            printf(f"[generate_test_traces] ⚠️  保存traces.json失败: {e}")


def main():

    args = parse_args()

    tests = collect_tests(
        project_root=args.project_root,
        output_dir=args.output_dir,
        random=args.random,
        random_seed=args.random_seed,
        max_tests=args.max_tests,
    )

    generate_test_traces(
        project_root=args.project_root,
        max_workers=args.max_workers,
        output_dir=args.output_dir,
        tests=tests,
    )


# excute: python trace.py --project-root /home/jiawei/CommitInsight/repos/scikit-learn --max-workers 16 --output-dir ./scikit-learn-test_cov
if __name__ == '__main__':

    main()
