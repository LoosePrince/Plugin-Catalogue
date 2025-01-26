import re
import json
import asyncio
from pytz import timezone
from datetime import datetime
from pathlib import Path
from asyncio.queues import Queue
from httpx import AsyncClient, Response


queue: Queue = Queue()
client: AsyncClient = AsyncClient()
client.headers = {'User-Agent': 'MCDReforged-Plugin-Scraper'}


def process_author(author_data):
    if isinstance(author_data, list):
        return author_data
    elif isinstance(author_data, str):
        return [author_data]
    elif isinstance(author_data, dict):
        return [author_data]


def process_description(desc_data):
    if isinstance(desc_data, dict):
        return {'en_us': desc_data.get('en_us', ''), 'zh_cn': desc_data.get('zh_cn', '')}
    elif isinstance(desc_data, str):
        return {'en_us': desc_data, 'zh_cn': desc_data}
    return {'en_us': '', 'zh_cn': ''}


async def request(url: str, retry_count: int = 3) -> Response:
    await queue.get()
    print(f'排队成功正在抓取 {url}')
    try:
        response = await client.get(url, timeout=20)
        response.raise_for_status()
        await queue.put(0)
        return response
    except Exception as error:
        print(f'抓取 {url} 失败 {error}，正在重试……')
    if retry_count > 0:
        return await request(url, retry_count - 1)
    await queue.put(0)
    return None


async def request_json(url: str, retry_count: int = 3) -> dict:
    response = await request(url, retry_count)
    if response is not None:
        return response.json()
    return None


async def fetch_plugin_info(plugin_name: str) -> bool:
    plugin_info = await request_json(f'https://cdn.jsdelivr.net/gh/MCDReforged/PluginCatalogue@master/plugins/{plugin_name}/plugin_info.json')
    if plugin_info.get('disable'):
        return None
    branch = plugin_info.pop('branch')
    repository = plugin_info.pop('repository')
    repository = f'{repository}/tree/{branch}'
    if relative := plugin_info.pop('related_path', None):
        if relative == '.':
            relative = None
        else:
            repository = f'{repository}/{relative}'
    plugin_info['repository_url'] = repository
    repository_user = repository.split('/')[3]
    repository_name = repository.split('/')[4]
    plugin_metadata = await request_json(f'https://cdn.jsdelivr.net/gh/{repository_user}/{repository_name}@{branch}{"/" + relative if relative else ""}/mcdreforged.plugin.json')
    if plugin_metadata is not None:
        if not plugin_info.get('version'):
            plugin_info['version'] = plugin_metadata.get('version')
        plugin_info['name'] = plugin_metadata.get('name')
        plugin_info['update_time'] = datetime.now(timezone('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
        meta_description = process_description(plugin_metadata.get('description', {}))
        original_description = process_description(plugin_info.get('description', {}))
        plugin_info['description'] = {
            'en_us': original_description['en_us'] or meta_description['en_us'],
            'zh_cn': original_description['zh_cn'] or meta_description['zh_cn']
        }
        if not plugin_info.get('dependencies'):
            plugin_info['dependencies'] = plugin_metadata.get('dependencies', {})
        response = await request(f'https://mcdreforged.com/zh-CN/plugin/{plugin_name}')
        match = re.search(rf'/plugin/{plugin_name}/release/([\d\.]+)', response.text)
        last_update_time = None
        latest_version = (match.group(1) if match else None) if match else None
        for line in response.text.split('\n'):
            line = line.replace(r'\"', '"')
            # 正则匹配所有符合格式的日期时间 {"date":"$DYYYY-MM-DDTHH:MM:SS.MMMZ"}
            matches = re.findall(r'{"date":"\$D(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z)"}', line)
            if matches:
                for match in matches:
                    last_update_time = match[:-1].replace('T', ' ')
        plugin_info['latest_version'] = latest_version
        plugin_info['last_update_time'] = last_update_time
        print(f'获取版本和更新时间成功 {plugin_name} {last_update_time} {latest_version}')
    print(f'获取插件信息成功 {plugin_name} 数据 {plugin_info}')
    return plugin_info


async def main():
    print('开始抓取插件列表……')
    for _ in range(2):
        await queue.put(0)
    tasks = []
    plugin_list = await request_json('https://api.github.com/repos/MCDReforged/PluginCatalogue/contents/plugins')
    for plugin in plugin_list:
        if plugin.get('type') != 'dir':
            continue
        plugin_name = plugin.get('name')
        tasks.append(asyncio.create_task(fetch_plugin_info(plugin_name)))
    return await asyncio.gather(*tasks)


if __name__ == '__main__':
    result = asyncio.run(main())
    result = [item for item in result if item is not None]

    print(f'抓取插件列表成功，共 {len(result)} 个插件，结果为 {result}')
    data_directory = Path('data')
    if not data_directory.exists():
        data_directory.mkdir()
    print(f'开始写入数据到文件 {data_directory / "plugins.json"}')
    data_file = data_directory / 'plugins.json'
    data_file.write_text(json.dumps(result, indent=4, ensure_ascii=False), encoding='Utf-8')
