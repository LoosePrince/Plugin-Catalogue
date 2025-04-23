import os
import json
import requests
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from datetime import datetime
import pytz
import re
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import urlparse
import posixpath
import argparse

# 配置参数
PLUGIN_PATH = "plugins"
DATA_PATH = "data"
PLUGINS_JSON_PATH = os.path.join(DATA_PATH, "plugins.json")
config_path = ".config"
GITHUB_TOKEN = None
SSL_VERIFY = True  # 设置为True如果网络环境正常
TIMEOUT = 15
RETRY_COUNT = 3

def parse_arguments():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description='插件目录爬取脚本')
    parser.add_argument('--timeout', type=int, default=TIMEOUT,
                        help=f'请求超时时间（秒），默认为{TIMEOUT}秒')
    parser.add_argument('--retry', type=int, default=RETRY_COUNT,
                        help=f'请求重试次数，默认为{RETRY_COUNT}次')
    parser.add_argument('--plugins-dir', type=str, default=PLUGIN_PATH,
                        help=f'插件目录路径，默认为{PLUGIN_PATH}')
    parser.add_argument('--data-dir', type=str, default=DATA_PATH,
                        help=f'数据目录路径，默认为{DATA_PATH}')
    
    return parser.parse_args()

# 读取GitHub PAT
def load_github_token():
    """加载GitHub令牌
    优先从.config文件中加载，如果没有则从环境变量中加载
    """
    if os.path.exists(config_path):
        with open(config_path, 'r') as config_file:
            for line in config_file:
                if line.startswith("github_pat="):
                    return line.strip().split("=", 1)[1]
    
    return os.environ.get('GITHUB_TOKEN')

def create_session(retry_count, timeout):
    """创建HTTP会话
    
    Args:
        retry_count: 重试次数
        timeout: 超时时间（秒）
        
    Returns:
        requests.Session: 配置好的会话对象
    """
    session = requests.Session()
    retries = Retry(
        total=retry_count,
        backoff_factor=0.5,
        status_forcelist=[500, 502, 503, 504]
    )
    session.mount('https://', HTTPAdapter(max_retries=retries))
    session.timeout = timeout
    return session

def get_beijing_time():
    """获取当前北京时间"""
    tz = pytz.timezone("Asia/Shanghai")
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

def parse_github_url(url):
    """解析GitHub URL，获取用户名和仓库名"""
    if not url:
        return None, None
    
    parsed = urlparse(url)
    if parsed.netloc != 'github.com':
        return None, None
    
    path_parts = parsed.path.strip('/').split('/')
    if len(path_parts) < 2:
        return None, None
    
    return path_parts[0], path_parts[1]

def get_file_content(session, owner, repo, path, branch='main'):
    """获取仓库中指定文件的内容"""
    url = f'https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}'
    response = session.get(url, headers=HEADERS, verify=SSL_VERIFY)
    
    if response.status_code == 404 and branch == 'main':
        # 如果main分支不存在，尝试master分支
        return get_file_content(session, owner, repo, path, 'master')
    
    if response.status_code != 200:
        return None
    
    content = response.json()
    if content.get('type') != 'file':
        return None
    
    # 获取文件内容并解码
    file_content = content.get('content', '')
    import base64
    try:
        decoded_content = base64.b64decode(file_content).decode('utf-8')
        return decoded_content
    except Exception as e:
        print(f"解析 {owner}/{repo}/{path} 的内容失败: {e}")
        return None

def check_repo_exists(session, owner, repo):
    """检查GitHub仓库是否存在
    
    Args:
        session: 请求会话
        owner: 仓库所有者
        repo: 仓库名称
        
    Returns:
        bool: 仓库是否存在并可访问
    """
    url = f'https://api.github.com/repos/{owner}/{repo}'
    try:
        response = session.get(url, headers=HEADERS, verify=SSL_VERIFY)
        return response.status_code == 200
    except Exception as e:
        print(f"检查仓库 {owner}/{repo} 是否存在时出错: {e}")
        return False

def find_plugin_json(session, owner, repo, branch='main', related_path=''):
    """在仓库中查找mcdreforged.plugin.json文件"""
    # 构建可能的插件文件路径
    possible_paths = []
    
    # 如果有指定相关路径，优先检查
    if related_path:
        possible_paths.append(f'{related_path}/mcdreforged.plugin.json')
    
    # 添加其他可能的路径
    possible_paths.extend([
        'mcdreforged.plugin.json',
        'src/mcdreforged.plugin.json',
        'plugin/mcdreforged.plugin.json',
        'plugins/mcdreforged.plugin.json'
    ])
    
    for path in possible_paths:
        content = get_file_content(session, owner, repo, path, branch)
        if content:
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                print(f"无法解析 {owner}/{repo}/{path} 的JSON内容")
                continue
    
    return None

def get_repo_info(session, owner, repo):
    """获取仓库的基本信息"""
    url = f'https://api.github.com/repos/{owner}/{repo}'
    response = session.get(url, headers=HEADERS, verify=SSL_VERIFY)
    
    if response.status_code != 200:
        print(f"获取 {owner}/{repo} 的仓库信息失败: {response.status_code}")
        return {}
    
    repo_data = response.json()
    license_data = repo_data.get('license') or {}
    return {
        'license': license_data.get('spdx_id'),  # 使用SPDX ID（缩写形式）
        'license_url': license_data.get('url'),
        'last_update_time': repo_data.get('pushed_at'),
        'stars': repo_data.get('stargazers_count', 0)
    }

def get_latest_version(session, owner, repo, plugin_id=None):
    """获取仓库的最新版本
    
    先尝试获取最新的正式release（非pre-release），如果没有，则获取最新的tag
    如果都没有，则返回None
    
    支持的tag格式:
    - <version>: 1.2.3
    - v<version>: v1.2.3
    - <plugin_id>-<version>: my_plugin-1.2.3
    - <plugin_id>-v<version>: my_plugin-v1.2.3
    """
    # 首先获取所有非预发布的releases
    url = f'https://api.github.com/repos/{owner}/{repo}/releases'
    response = session.get(url, headers=HEADERS, verify=SSL_VERIFY)
    
    if response.status_code == 200:
        releases = response.json()
        # 过滤掉预发布版本
        non_prerelease = [r for r in releases if not r.get('prerelease', False)]
        
        if non_prerelease:
            # 获取最新的非预发布版本
            latest_release = non_prerelease[0]
            tag_name = latest_release.get('tag_name', '')
            version = extract_version_from_tag(tag_name, plugin_id)
            return version
        else:
            print(f"没有找到非预发布版本，尝试从tags中提取")
    else:
        print(f"获取 {owner}/{repo} 的发布信息失败: {response.status_code}")
    
    # 如果没有找到有效的非预发布release，则获取所有tags
    url = f'https://api.github.com/repos/{owner}/{repo}/tags'
    response = session.get(url, headers=HEADERS, verify=SSL_VERIFY)
    
    if response.status_code == 200:
        tags = response.json()
        if tags:
            latest_tag = tags[0]
            tag_name = latest_tag.get('name', '')
            version = extract_version_from_tag(tag_name, plugin_id)
            return version
    
    return None

def extract_version_from_tag(tag_name, plugin_id=None):
    """从标签名中提取版本号
    
    支持的格式:
    - <version>: 1.2.3
    - v<version>: v1.2.3
    - <plugin_id>-<version>: my_plugin-1.2.3
    - <plugin_id>-v<version>: my_plugin-v1.2.3
    """
    if not tag_name:
        return None
    
    # 判断是否包含插件id前缀
    if plugin_id and tag_name.startswith(f"{plugin_id}-"):
        # 移除插件id前缀
        tag_name = tag_name[len(plugin_id)+1:]
    
    # 移除可能的v前缀
    if tag_name.startswith('v'):
        tag_name = tag_name[1:]
    
    # 尝试解析版本号（简单的数字和点号格式验证）
    if re.match(r'^\d+(\.\d+)*$', tag_name):
        return tag_name
    
    # 如果以上格式都不匹配，尝试从标签名中查找版本号模式
    match = re.search(r'(\d+\.\d+(\.\d+)*)', tag_name)
    if match:
        return match.group(1)
    
    return tag_name  # 如果无法解析，返回原始标签名

def get_downloads_count(session, owner, repo):
    """获取仓库的下载次数（releases总和）
    
    只计算.mcdr和.pyz文件的下载次数
    """
    url = f'https://api.github.com/repos/{owner}/{repo}/releases'
    response = session.get(url, headers=HEADERS, verify=SSL_VERIFY)
    
    if response.status_code != 200:
        print(f"获取 {owner}/{repo} 的发布信息失败: {response.status_code}")
        return 0
    
    releases = response.json()
    total_downloads = 0
    
    for release in releases:
        for asset in release.get('assets', []):
            asset_name = asset.get('name', '').lower()
            # 只计算.mcdr和.pyz文件的下载次数
            if asset_name.endswith('.mcdr') or asset_name.endswith('.pyz'):
                download_count = asset.get('download_count', 0)
                total_downloads += download_count
                print(f"计算下载: {asset_name} = {download_count}次")
    
    print(f"总下载次数: {total_downloads}")
    return total_downloads

def get_plugin_info_from_folder(plugin_folder):
    """从插件文件夹中读取plugin_info.json"""
    plugin_info_path = os.path.join(plugin_folder, 'plugin_info.json')
    if not os.path.exists(plugin_info_path):
        return None
    
    try:
        with open(plugin_info_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"读取插件信息文件失败: {e}")
        return None

def resolve_readme_path(related_path, readme_path):
    """解析README路径，相对于related_path"""
    print(f"解析README路径: related_path={related_path}, readme_path={readme_path}")
    
    if not readme_path:
        print(f"没有指定README路径，使用默认路径: README.md")
        return "README.md"  # 默认为根目录的README.md
    
    # 使用posixpath处理路径，确保使用/而不是\
    if related_path:
        # 如果有相关路径，并且readme_path是相对路径
        if readme_path.startswith("../") or readme_path.startswith("./"):
            # 组合路径并规范化
            full_path = posixpath.normpath(posixpath.join(related_path, readme_path))
            print(f"路径组合结果: {full_path}")
            if full_path.startswith("/"):
                full_path = full_path[1:]  # 移除开头的/
                print(f"移除开头的/: {full_path}")
            return full_path
    
    # 如果readme_path不是相对路径，或者没有related_path
    print(f"返回原始路径: {readme_path}")
    return readme_path

def process_plugin(plugin_folder, session):
    """处理单个插件文件夹，获取并更新插件信息"""
    try:
        plugin_id = os.path.basename(plugin_folder)
        print(f"\n=============== 处理插件: {plugin_id} ===============")
        
        # 从插件文件夹获取信息
        local_info = get_plugin_info_from_folder(plugin_folder)
        if not local_info:
            print(f"插件 {plugin_id} 没有找到plugin_info.json文件，跳过")
            return None
        
        # 加载现有的插件信息（如果存在）
        existing_data = {}
        if os.path.exists(PLUGINS_JSON_PATH):
            with open(PLUGINS_JSON_PATH, 'r', encoding='utf-8') as f:
                try:
                    plugins_data = json.load(f)
                    for plugin in plugins_data:
                        if plugin.get('id') == plugin_id:
                            existing_data = plugin
                            break
                except json.JSONDecodeError:
                    print(f"无法解析 {PLUGINS_JSON_PATH}，将使用空数据")
        
        # 获取仓库信息
        repository_url = local_info.get('repository')
        if not repository_url:
            print(f"插件 {plugin_id} 没有仓库URL信息，跳过")
            return None
        
        owner, repo = parse_github_url(repository_url)
        if not owner or not repo:
            print(f"插件 {plugin_id} 的仓库URL格式不正确: {repository_url}")
            return None
        
        branch = local_info.get('branch', 'main')
        related_path = local_info.get('related_path', '')
        
        # 验证仓库是否存在
        # 先检查仓库是否存在
        repo_exists = check_repo_exists(session, owner, repo)
        if not repo_exists:
            print(f"仓库 {owner}/{repo} 不存在或无法访问，使用本地信息构建最小数据")
            # 构建一个最小的插件信息
            return {
                'id': plugin_id,
                'name': local_info.get('name', plugin_id),
                'version': existing_data.get('version', '0.0.0'),
                'description': existing_data.get('description', {'en_us': '暂无描述', 'zh_cn': '暂无描述'}),
                'dependencies': {},
                'labels': local_info.get('labels', []),
                'repository_url': repository_url,
                'update_time': get_beijing_time(),
                'latest_version': existing_data.get('latest_version', '0.0.0'),
                'license': existing_data.get('license'),
                'license_url': existing_data.get('license_url'),
                'downloads': existing_data.get('downloads', 0),
                'readme_url': f'https://raw.githubusercontent.com/{owner}/{repo}/{branch}/README.md',
                'last_update_time': existing_data.get('last_update_time'),
                'authors': local_info.get('authors', [])
            }
        
        # 获取插件信息
        plugin_info = find_plugin_json(session, owner, repo, branch, related_path)
        if not plugin_info:
            print(f"无法获取插件 {plugin_id} 的信息，尝试使用现有数据")
            # 如果没有从GitHub获取到插件信息，则构建一个最小的插件信息集
            plugin_info = {
                'id': plugin_id,
                'name': local_info.get('name', plugin_id),
                'version': existing_data.get('version', '0.0.0'),
                'description': existing_data.get('description', {'en_us': '暂无描述', 'zh_cn': '暂无描述'})
            }
        
        # 确认插件ID（优先使用plugin_info中的id）
        actual_plugin_id = plugin_info.get('id', plugin_id)
        
        # 获取仓库信息 - 可能会失败，使用默认值或现有值
        try:
            repo_info = get_repo_info(session, owner, repo) or {}
        except Exception as e:
            print(f"获取仓库信息失败: {e}")
            repo_info = {}
        
        # 获取下载次数 - 可能会失败，使用默认值或现有值
        try:
            downloads = get_downloads_count(session, owner, repo)
        except Exception as e:
            print(f"获取下载次数失败: {e}")
            downloads = existing_data.get('downloads', 0)
        
        # 获取最新版本号 - 可能会失败，使用默认值或现有值
        try:
            latest_version = get_latest_version(session, owner, repo, actual_plugin_id)
            if not latest_version:
                latest_version = plugin_info.get('version', existing_data.get('latest_version', '0.0.0'))
        except Exception as e:
            print(f"获取最新版本失败: {e}")
            latest_version = plugin_info.get('version', existing_data.get('latest_version', '0.0.0'))
        
        # 处理README路径
        readme_path = None
        if 'introduction' in local_info:
            # 优先使用中文README，如果没有则使用英文README
            intro = local_info['introduction']
            if isinstance(intro, dict):
                readme_path = intro.get('zh_cn') or intro.get('en_us')
            else:
                readme_path = intro
        
        print(f"原始README路径: {readme_path}")
        resolved_readme_path = resolve_readme_path(related_path, readme_path) or "README.md"
        readme_url = f'https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{resolved_readme_path}'
        print(f"最终README URL: {readme_url}")
        
        # 构建插件数据
        plugin_data = {
            'id': actual_plugin_id,
            'name': plugin_info.get('name', actual_plugin_id),
            'version': plugin_info.get('version', existing_data.get('version', '0.0.0')),
            'description': plugin_info.get('description', existing_data.get('description', {'en_us': '暂无描述', 'zh_cn': '暂无描述'})),
            'dependencies': plugin_info.get('dependencies', existing_data.get('dependencies', {})),
            'labels': local_info.get('labels', existing_data.get('labels', [])),
            'repository_url': plugin_info.get('link', repository_url),
            'update_time': get_beijing_time(),
            'latest_version': latest_version,
            'license': repo_info.get('license', existing_data.get('license')),
            'license_url': repo_info.get('license_url', existing_data.get('license_url')),
            'downloads': downloads if downloads > 0 else existing_data.get('downloads', 0),
            'readme_url': readme_url,
            'last_update_time': repo_info.get('last_update_time', existing_data.get('last_update_time'))
        }
        
        # 处理作者信息
        # 优先使用本地作者信息，如果没有，再从插件json获取
        authors = []
        if 'authors' in local_info:
            authors = local_info['authors']
        elif 'author' in plugin_info:
            plugin_authors = plugin_info.get('author', [])
            if isinstance(plugin_authors, str):
                plugin_authors = [plugin_authors]
            
            for author in plugin_authors:
                authors.append({
                    'name': author,
                    'link': f'https://github.com/{author}' if '/' not in author else author
                })
        
        # 如果没有获取到作者信息，保留原有的
        if not authors and 'authors' in existing_data:
            authors = existing_data['authors']
        
        plugin_data['authors'] = authors
        
        return plugin_data
    
    except Exception as e:
        import traceback
        print(f"处理插件 {os.path.basename(plugin_folder)} 时出错: {e}")
        print(traceback.format_exc())  # 打印完整的堆栈跟踪
        
        # 尝试构建一个最小的数据集
        try:
            local_info = get_plugin_info_from_folder(plugin_folder) or {}
            plugin_id = os.path.basename(plugin_folder)
            
            # 构建最小数据
            return {
                'id': plugin_id,
                'name': local_info.get('name', plugin_id),
                'version': '0.0.0',
                'description': {'en_us': '暂无描述', 'zh_cn': '暂无描述'},
                'dependencies': {},
                'labels': local_info.get('labels', []),
                'repository_url': local_info.get('repository', ''),
                'update_time': get_beijing_time(),
                'latest_version': '0.0.0',
                'downloads': 0,
                'authors': local_info.get('authors', [])
            }
        except:
            return None

def scan_plugins(plugin_path, session):
    """扫描插件目录，获取所有插件信息"""
    if not os.path.exists(plugin_path):
        print(f"插件目录 {plugin_path} 不存在")
        return []
    
    plugins = []
    
    for item in os.listdir(plugin_path):
        plugin_folder = os.path.join(plugin_path, item)
        if os.path.isdir(plugin_folder):
            print(f"处理插件: {item}")
            plugin_data = process_plugin(plugin_folder, session)
            if plugin_data:
                plugins.append(plugin_data)
    
    return plugins

def update_plugins_json(plugin_path, data_path, plugins_json_path):
    """更新plugins.json文件"""
    # 确保数据目录存在
    if not os.path.exists(data_path):
        os.makedirs(data_path)
    
    # 加载现有的插件数据
    existing_plugins = []
    if os.path.exists(plugins_json_path):
        with open(plugins_json_path, 'r', encoding='utf-8') as f:
            try:
                existing_plugins = json.load(f)
            except json.JSONDecodeError:
                print(f"无法解析 {plugins_json_path}，将创建新文件")
    
    # 创建会话
    session = create_session(RETRY_COUNT, TIMEOUT)
    
    # 扫描插件获取新数据
    new_plugins = scan_plugins(plugin_path, session)
    
    # 更新或添加插件数据
    updated_plugins = existing_plugins.copy()
    for new_plugin in new_plugins:
        found = False
        for i, existing_plugin in enumerate(updated_plugins):
            if existing_plugin.get('id') == new_plugin.get('id'):
                updated_plugins[i] = new_plugin
                found = True
                break
        
        if not found:
            updated_plugins.append(new_plugin)
    
    # 保存更新后的插件数据
    with open(plugins_json_path, 'w', encoding='utf-8') as f:
        json.dump(updated_plugins, f, ensure_ascii=False, indent=2)
    
    print(f"已更新 {plugins_json_path}，共 {len(updated_plugins)} 个插件")

def main():
    """主函数"""
    # 解析命令行参数
    args = parse_arguments()
    
    global GITHUB_TOKEN, HEADERS, TIMEOUT, RETRY_COUNT, PLUGIN_PATH, DATA_PATH, PLUGINS_JSON_PATH
    
    # 更新全局配置
    TIMEOUT = args.timeout
    RETRY_COUNT = args.retry
    PLUGIN_PATH = args.plugins_dir
    DATA_PATH = args.data_dir
    PLUGINS_JSON_PATH = os.path.join(DATA_PATH, "plugins.json")
    
    # 加载GitHub令牌
    GITHUB_TOKEN = load_github_token()
    if not GITHUB_TOKEN:
        print("错误: 未找到GitHub令牌，请检查.config文件或GITHUB_TOKEN环境变量")
        return 1
    
    # 设置请求头
    HEADERS = {
        'User-Agent': 'MCDReforged-Plugin-Scraper',
        'Authorization': f'token {GITHUB_TOKEN}',
        'Accept': 'application/vnd.github.v3+json'
    }
    
    print(f"开始更新插件数据，超时时间: {TIMEOUT}秒，重试次数: {RETRY_COUNT}")
    print(f"插件目录: {PLUGIN_PATH}, 数据目录: {DATA_PATH}")
    
    # 更新plugins.json
    update_plugins_json(PLUGIN_PATH, DATA_PATH, PLUGINS_JSON_PATH)
    
    return 0

if __name__ == "__main__":
    exit_code = main()
    exit(exit_code)
