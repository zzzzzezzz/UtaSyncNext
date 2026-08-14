# Reference Lyrics

测试时采用“一个视频一个歌词本”的文本文件。歌词文件可能包含版权内容或私人整理信息，因此本目录中的 `*.txt` 默认只保存在本机，不进入 Git。

全流程测试可通过以下参数读取本地歌词本：

```powershell
.\.venv\Scripts\python.exe tools\smoke_full_pipeline.py --lyrics-file "test_assets\lyrics_refs\your_video.txt" ...
```
