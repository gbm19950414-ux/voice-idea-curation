# Voice Idea Curation

本地录音想法的识别、来源保留、疑点整理与项目交接工具。Local voice intake, source-linked review and scoped project routing.

公开的是通用 skill、标准库脚本和虚构／模拟测试；录音、转写、词汇纠正、inbox、个人设置和运行时均不公开。工具不是一个语音模型；语义整理由使用者与当前助手共同完成。自动交接需要接收项目实际批准的规则，不因安装工具就获得写入或消息发送权限。

## 先运行离线例子

需要 Python 3.10 或更新。以下不会上传音频、下载模型或写私人记录：

```sh
python3 -m unittest discover -s .agents/skills/voice-idea-curation/scripts -p 'test_*.py' -v
python3 examples/review_demo.py
```

第二条从纯虚构识别文字生成 demo-output/review.md 和 review.json。它演示把识别原文、整理稿与待确认疑点分开，不伪造人工确认。

## 使用真实录音前

通用入口在 .agents/skills/voice-idea-curation/SKILL.md，格式与调用说明在 references/。真实识别需另行配置 ffmpeg/ffprobe、固定版本的本地 whisper.cpp 与模型；Apple 识别仅适用受支持的本机环境。不要直接把个人 inbox 放进此公开仓。bootstrap_local_asr.py 会下载依赖，只有实际选择并需要这一识别路线时才调用。

去重、有限重试、版本化复核和基于来源的路由与原意确认各有边界；机器识别置信度不是准确率。离线 mock 测试通过也不等于真实录音识别质量已验证。参考 local-recognition.md 和 project-interface.md。

原开发来源是 voice-idea-curation；公开版本从同一来源导出，PUBLIC_EXPORT.json 记录提交与文件哈希，排除原 Git 历史。项目尚未指定覆盖全部原创代码的复用许可；公开上传不自动授予开源复用许可。
