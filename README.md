# 成语猜猜乐游戏插件

一个有趣的微信成语猜谜游戏插件，支持智能提示和排行榜功能。

## 功能特点

1. 每轮游戏包含5道题目
2. 每题限时30秒
3. 支持智能提示和追问功能
4. 实时排行榜系统
5. 支持群聊和私聊
6. 支持OpenAI智能解析和提示

## 游戏命令

1. `猜成语` 或 `开始游戏` - 开始新游戏
2. `提示` - 获取智能提示
3. `问+问题` - 获取更多提示（例如：问意思、问出处）
4. `我猜xxx` - 提交答案（xxx为猜测的成语）
5. `下一题` - 跳过当前题目
6. `结束游戏` - 结束当前游戏
7. `历史排行榜` - 查看成绩排行
8. `猜成语帮助` - 查看帮助信息

## 管理员命令

1. `重置排行榜` - 清空所有排行榜数据

## 游戏规则

1. 每轮游戏包含5道题目
2. 每题限时30秒，超时自动进入下一题
3. 答对一题得1分
4. 可以使用提示和追问功能获取帮助
5. 支持跳过难题，直接进入下一题
6. 游戏结束后显示得分和排行榜

## 配置说明

在 `config.json` 中可以配置以下参数：

```json
{
    "api_url": "https://xiaoapi.cn/API/game_ktccy.php",
    "cache_timeout": 300,
    "questions_per_round": 5,
    "leaderboard_size": 10,
    "enable_openai": true,
    "openai_api_key": "",
    "openai_model": "gpt-3.5-turbo",
    "game_settings": {
        "time_limit": 30,
        "auto_next_delay": 1,
        "correct_answer_delay": 3
    }
}
```

### 配置参数说明

- `api_url`: 成语题目API地址
- `cache_timeout`: 缓存超时时间（秒）
- `questions_per_round`: 每轮题目数量
- `leaderboard_size`: 排行榜显示人数
- `enable_openai`: 是否启用OpenAI功能
- `openai_api_key`: OpenAI API密钥
- `openai_model`: OpenAI模型名称
- `game_settings`:
  - `time_limit`: 每题限时（秒）
  - `auto_next_delay`: 自动进入下一题的延迟（秒）
  - `correct_answer_delay`: 答对后进入下一题的延迟（秒）

## 安装说明

1. 将插件文件夹复制到 `plugins` 目录下
2. 配置 `config.json` 文件
3. 重启应用即可使用

## 注意事项

1. 确保API地址可以正常访问
2. 如需使用OpenAI功能，请配置有效的API密钥
3. 建议定期备份排行榜数据
4. 游戏进行中可随时结束或跳过当前题目

## 更新日志

### v1.0
- 初始版本发布
- 支持基本的成语猜谜功能
- 添加排行榜系统
- 集成OpenAI智能提示功能 
