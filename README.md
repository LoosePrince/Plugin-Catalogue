# Plugin-Catalogue
MCDR 插件仓库（非官）

## 项目介绍

这是一个非官方的 MCDR (MCDReforged) 插件仓库，用于收集、整理和展示各种 MCDR 插件信息。本项目通过自动化脚本从 GitHub 获取插件信息，并生成统一的插件数据文件，方便用户查找和使用各类 MCDR 插件。

## 功能特点

- 自动从 GitHub 仓库抓取插件信息
- 支持插件版本、下载量、许可证等信息的获取
- 提供统一的 JSON 格式插件数据
- 支持多语言插件描述
- 被 [WebUI](https://github.com/LoosePrince/PF-MCDR-WebUI) 插件支持，实现一键安装功能

## 目录结构

```
Plugin-Catalogue/main
├── data/                # 存放生成的数据文件
│   ├── index.html       # 插件信息汇总页面
│   └── README.md        # 分支描述
├── plugins/             # 存放各插件信息目录
│   └── [plugin_id]/     # 每个插件的信息目录
│       └── plugin_info.json  # 基本信息配置
├── scripts/             # 脚本文件
│   └── plugin_scraper.py  # 插件信息抓取脚本
├── .config              # 配置文件（包含 GitHub PAT）仅在本地使用，请勿提交自己的密钥到公开领域
└── README.md            # 主分支描述
```

```
Plugin-Catalogue/meta
├── data/                # 存放生成的数据文件
│   ├── index.html       # 插件信息汇总页面（从main分支data/index.html复制）
│   └── plugins.json     # 插件信息汇总
└── README.md            # 分支描述（从main分支data/README.md复制）
```

## 如何使用本仓库

您可以在 [WebUI](https://github.com/LoosePrince/PF-MCDR-WebUI) 插件的网页设置中添加本仓库（目前已内置WebUI插件中，无需进行此步）

```
https://looseprince.github.io/Plugin-Catalogue/plugins.json
```

然后在在线插件的仓库列表中选择本仓库，即可看到所有插件信息

也可以在 `https://looseprince.github.io/Plugin-Catalogue/` 查看所有插件信息

## 添加新插件（提交您的插件到本仓库）

要添加新插件，请在 `plugins` 目录下创建以插件 ID 命名的文件夹，并在其中创建 `plugin_info.json` 文件：

符合官方标准：https://docs.mcdreforged.com/zh-cn/latest/plugin_dev/plugin_catalogue.html#submit-plugin

```json
{
  "name": "插件名称",
  "repository": "https://github.com/用户名/仓库名",
  "branch": "main",
  "related_path": "",
  "introduction": {
    "zh_cn": "README_CN.md",
    "en_us": "README.md"
  },
  "labels": ["工具", "管理"],
  "authors": [
    {
      "name": "作者名",
      "link": "https://github.com/作者名"
    }
  ]
}
```

## 搭建自己的仓库

### 环境配置

1. 克隆本仓库
```
git clone https://github.com/LoosePrince/Plugin-Catalogue.git
cd Plugin-Catalogue
```

2. 安装依赖
```
pip install requests pytz
```

3. 在环境变量中添加 GitHub 令牌

### 运行插件信息抓取脚本

```
python scripts/plugin_scraper.py
```

可选参数：
- `--timeout` - 设置请求超时时间（默认15秒）
- `--retry` - 设置请求重试次数（默认3次）
- `--plugins-dir` - 设置插件目录路径（默认 "plugins"）
- `--data-dir` - 设置数据目录路径（默认 "data"）

例如：
```
python scripts/plugin_scraper.py --timeout 30 --retry 5
```

访问 `https://your-github-io.github.io/Plugin-Catalogue/` 即可看到所有插件信息和其它文档

## 许可

如果需要使用本项目搭建自己的插件仓库，请遵守以下许可：

允许任何人使用此项目，但请注明出处。
> 建议使用fork的方式使用本项目

## 贡献指南

欢迎提交 Pull Request 或 Issue 来完善此插件仓库。添加新插件时，请确符合官方标准。
