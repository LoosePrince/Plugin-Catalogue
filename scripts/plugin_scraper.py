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

# 配置参数
GITHUB_API = "https://api.github.com/repos/MCDReforged/PluginCatalogue/contents/plugins"
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')  # 与YAML中的名称一致
HEADERS = {
    'User-Agent': 'MCDReforged-Plugin-Scraper',
    'Authorization': f'token {GITHUB_TOKEN}',
    'Accept': 'application/vnd.github.v3+json'
}
SSL_VERIFY = True  # 设置为True如果网络环境正常
TIMEOUT = 15
RETRY_COUNT = 3

def create_session():
    session = requests.Session()
    retries = Retry(
        total=RETRY_COUNT,
        backoff_factor=0.5,
        status_forcelist=[500, 502, 503, 504]
    )
    session.mount('https://', HTTPAdapter(max_retries=retries))
    return session

def get_beijing_time():
    """获取当前北京时间"""
    tz = pytz.timezone("Asia/Shanghai")
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

def fetch_version(plugin_name):
    """获取插件版本"""
    url = f"https://mcdreforged.com/zh-CN/plugin/{plugin_name}?_rsc=1rz10"
    try:
        response = requests.get(url, timeout=5, verify=SSL_VERIFY)
        response.raise_for_status()
        match = re.search(rf'/plugin/{plugin_name}/release/([\d\.]+)', response.text)
        print(f"获取版本成功 {plugin_name}: {match.group(1)}")
        return match.group(1) if match else None
    except Exception as e:
        print(f"获取版本失败 {plugin_name}: {str(e)}")
        return None

def get_plugin_versions(plugin_dict):
    """
    获取插件版本信息
    """
    results = {}
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_plugin = {executor.submit(fetch_version, name): name for name in plugin_dict.values()}
        for future in as_completed(future_to_plugin):
            plugin_name = future_to_plugin[future]
            try:
                version = future.result()
                results[plugin_name] = version
            except Exception:
                results[plugin_name] = None

    return results

def build_jsdelivr_url(repo_url):
    """从仓库URL构造mcdreforged.plugin.json的JsDelivr地址"""
    # 允许仓库名与tree之间存在多余斜杠，并提取tree后的全部内容
    pattern = r"https://github\.com/([^/]+)/([^/]+)(?:/+tree/+)(.*)"
    match = re.match(pattern, repo_url)
    if not match:
        return None
    
    user, repo, tree_part = match.groups()
    # 分割tree后的部分并过滤空段
    parts = [p for p in tree_part.split('/') if p.strip()]
    if not parts:
        return None  # 无有效分支名
    
    branch = parts[0]
    path_parts = parts[1:]
    # 构造路径并添加文件名（自动处理斜杠）
    file_name = "mcdreforged.plugin.json"
    if path_parts:
        # 使用join自动处理路径中的斜杠
        full_path = "/".join(path_parts) + f"/{file_name}"
    else:
        full_path = file_name
    
    # 最终URL拼接（自动处理可能的拼接斜杠问题）
    return f"https://cdn.jsdelivr.net/gh/{user}/{repo}@{branch}/{full_path}"

def fetch_plugin_metadata(session, jsdelivr_url):
    """获取插件元数据"""
    try:
        response = session.get(
            jsdelivr_url,
            headers={'User-Agent': 'MCDReforged-Plugin-Scraper'},
            timeout=TIMEOUT,
            verify=SSL_VERIFY
        )
        response.raise_for_status()
        return json.loads(response.text)
    except Exception as e:
        print(f"获取元数据失败: {str(e)}")
        return None
    

def process_author(author_data):
    """统一处理作者信息格式"""
    if isinstance(author_data, list):
        return author_data
    elif isinstance(author_data, str):
        return [author_data]
    elif isinstance(author_data, dict):
        return [author_data]
    else:
        return None
    

def process_description(desc_data):
    """统一处理描述信息格式"""
    if isinstance(desc_data, dict):
        return {
            "en_us": desc_data.get('en_us', ''),
            "zh_cn": desc_data.get('zh_cn', '')
        }
    elif isinstance(desc_data, str):
        return {"en_us": desc_data, "zh_cn": desc_data}
    else:
        return {"en_us": "", "zh_cn": ""}
    
def merge_plugin_data(original_data, metadata):
    """严格保留原始数据优先的合并策略"""
    merged = original_data.copy()
    
    # 版本信息（仅当原始数据缺失时补充）
    if not merged.get('version'):
        merged['version'] = metadata.get('version')
    
    # 插件名称（保留原始数据）
    merged['name'] = original_data.get('name') or metadata.get('name')
    
    # 作者信息（合并而非覆盖）
    # original_authors = process_author(original_data.get('authors', []))
    # meta_authors = process_author(metadata.get('author', []))
    # merged['authors'] = unique_author_merge(original_authors, meta_authors)
    
    # 描述信息（补充翻译内容）
    merged_desc = process_description(original_data.get('description', {}))
    meta_desc = process_description(metadata.get('description', {}))
    merged['description'] = {
        "en_us": merged_desc['en_us'] or meta_desc['en_us'],
        "zh_cn": merged_desc['zh_cn'] or meta_desc['zh_cn']
    }
    
    # 依赖信息（保留原始数据优先）
    if not merged.get('dependencies'):
        merged['dependencies'] = metadata.get('dependencies')
    
    return merged

def unique_author_merge(original, meta):
    """去重合并作者信息"""
    seen = set()
    result = []
    
    # 保留原始顺序和格式
    for author in original + meta:
        key = str(author).lower()
        if key not in seen:
            seen.add(key)
            result.append(author)
    
    return result

def build_repo_url(plugin_info):
    """构造规范的仓库链接"""
    try:
        base_url = f"{plugin_info['repository']}/tree/{plugin_info['branch']}"
        
        # 处理路径部分
        if 'related_path' in plugin_info:
            # 规范化路径处理
            path = str(plugin_info['related_path']).strip()
            path = path.replace('\\', '/').strip('/')
            
            # 过滤非法路径
            if path and path != '.':
                # 使用urljoin确保路径正确
                return requests.compat.urljoin(base_url + '/', path)
        
        return base_url
    except Exception as e:
        print(f"仓库链接构造失败: {str(e)}")
        return None
    
def get_plugins_info():
    """获取所有插件信息"""
    plugins = []
    plugin_dict = {}
    
    try:
        session = create_session()
        # 使用GitHub API获取插件列表（保持原样）
        response = session.get(
            GITHUB_API,
            headers=HEADERS,
            timeout=TIMEOUT,
            verify=SSL_VERIFY
        )
        response.raise_for_status()
        
        for item in response.json():
            if item['type'] == 'dir':
                plugin_name = item['name']
                try:
                    # 构造JsDelivr的URL
                    jsdelivr_url = f"https://cdn.jsdelivr.net/gh/MCDReforged/PluginCatalogue@master/plugins/{plugin_name}/plugin_info.json"
                    # 发送请求到JsDelivr，不使用认证头
                    info_response = session.get(
                        jsdelivr_url,
                        headers={'User-Agent': 'MCDReforged-Plugin-Scraper'},
                        timeout=TIMEOUT,
                        verify=SSL_VERIFY
                    )
                    info_response.raise_for_status()
                    
                    content = info_response.text
                    plugin_info = json.loads(content)
                    
                    # 构造仓库链接（清理冗余路径）
                    repo_url = f"{plugin_info['repository']}/tree/{plugin_info['branch']}"
                    if plugin_info.get('related_path'):
                        related_path = os.path.normpath(plugin_info['related_path']).replace('\\', '/').strip('/')
                        if related_path != '.':
                            repo_url += f"/{related_path}"

                    # 构造JsDelivr元数据地址
                    meta_url = build_jsdelivr_url(repo_url)
                    if not meta_url:
                        print(f"无效仓库地址: {repo_url}")
                        continue
                        
                    # 获取元数据
                    metadata = fetch_plugin_metadata(session, meta_url)
                    
                    # 基础数据必须存在的字段
                    plugin_data = {
                        "id": plugin_info['id'],  # 必须存在
                        "authors": process_author(plugin_info.get('authors', [])),
                        "repository_url": build_repo_url(plugin_info),
                        "labels": plugin_info.get('labels', []),
                        "name": plugin_info.get('name'),  # 原始数据可能没有
                        "version": None,
                        "description": process_description(plugin_info.get('description', {})),
                        "dependencies": None,
                        "update_time": get_beijing_time(),  # 使用北京时间
                        "latest_version": None
                    }

                    # if metadata:
                    #     plugin_data = merge_plugin_data(plugin_data, metadata)
                    #     print(f"成功合并 {plugin_name} 元数据")
                    # else:
                    #     print(f"未找到 {plugin_name} 元数据文件")
                    
                    # plugins.append(plugin_data)
                    try:
                        # 构造仓库链接
                        repo_url = build_repo_url(plugin_info)
                        plugin_data['repository_url'] = repo_url if repo_url else None
                        
                        # 合并元数据
                        if repo_url:
                            meta_url = build_jsdelivr_url(repo_url)
                            metadata = fetch_plugin_metadata(session, meta_url) if meta_url else None
                            
                            # 合并元数据（不影响已存在的有效字段）
                            if metadata:
                                plugin_data = merge_plugin_data(plugin_data, metadata)
                        
                        # 确保最终数据结构
                        plugin_data['authors'] = process_author(plugin_data['authors']) or []
                        plugin_data['description'] = process_description(plugin_data['description'])
                        
                    except Exception as e:
                        print(f"插件数据处理异常: {str(e)}")
                        # 保留已获取的基础信息
                    
                    plugins.append(plugin_data)
                    plugin_dict[plugin_info['id']] = plugin_info['id']
                    print(f"获取 {plugin_name} 信息成功")
                    
                except Exception as e:
                    print(f"获取 {plugin_name} 信息失败: {str(e)}")
                    continue

        # 获取版本信息并填充 latest_version
        versions = get_plugin_versions(plugin_dict)
        for plugin in plugins:
            plugin["latest_version"] = versions.get(plugin["id"], None)

    except Exception as e:
        print(f"主流程错误: {str(e)}")

    return plugins

def save_plugins_data(plugins):
    """
    保存插件数据到JSON文件
    """
    output_dir = "./data"
    os.makedirs(output_dir, exist_ok=True)
    
    output_path = os.path.join(output_dir, "plugins.json")
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(plugins, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    # 测试网络连接
    try:
        test_response = requests.get("https://api.github.com", timeout=5, verify=SSL_VERIFY)
        print("GitHub API连接测试:", "成功" if test_response.ok else "失败")
    except Exception as e:
        print("网络连接测试失败:", str(e))
        exit(1)

    plugins_info = get_plugins_info()
    save_plugins_data(plugins_info)
    print(f"成功保存 {len(plugins_info)} 个插件信息到 /data/plugins.json")