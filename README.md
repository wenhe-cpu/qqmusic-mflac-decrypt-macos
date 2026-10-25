# QQ 音乐 mflac 解密工具（macOS）

一键把 QQ 音乐 Mac 客户端下载的加密音频（`.mflac` / `.mgg`）转换成可直接播放的 `.flac` / `.ogg`。

纯 Python 标准库实现，**免编译、零第三方依赖**，不需要 Homebrew、cmake 或 ffmpeg。

```
输入：7 个文件
输出：/Users/you/Desktop/Decoded_Music

[1/7] OK  曹格-悲歌王.mflac -> 曹格-悲歌王.flac
...
转换完成，一共转换了 7 首。
```

## 特性

- **免编译**：只要系统自带的 `python3`（macOS 自带 3.9+ 即可），无需拉源码、无需 CMake。
- **自动换密钥**：读取本机 QQ 音乐的登录态，向官方接口换取 ekey，不需要手动填密钥。
- **路径安全**：所有路径均以双引号包裹，空格、中文、括号、emoji 都不会出错。
- **批处理健壮**：单个文件失败不会中断整批，结束时统一统计并列出失败原因。
- **不写坏文件**：解密后先校验音频文件头（`fLaC` / `OggS` / `ID3`），密钥不对直接报错，绝不产出噪声文件。
- **本地缓存**：ekey 缓存在 `~/Library/Caches/qqmusic-decrypt/`，重复运行不会重复请求接口。

## 支持格式

| 封装类型 | 典型扩展名 | 密钥来源 |
| --- | --- | --- |
| PC `musicex` | `.mflac` `.mgg` | 本机登录态 → 官方接口换 ekey |
| PC 旧封装 | `.mflac` `.mgg` | 文件尾部内嵌 ekey |
| Android `QTag` | `.mflac` `.mgg` | 文件尾部内嵌 ekey（可离线） |
| 经典 QMCv1 | `.qmcflac` `.qmc0` `.qmc3` `.qmcogg` `.tkm` | 静态密钥表 |

输出格式按解密后的真实文件头判断，而不是简单改后缀。

## 原理

1. 解析文件尾部的封装结构，取出 `media_mid` 与媒体文件名；
2. 从 `~/Library/Containers/com.tencent.QQMusicMac/Data/Library/Preferences/com.tencent.QQMusicMac.plist`
   的 `AutoLoginUserInfo` 中读取 `uin` 与 `authst`；
3. 调用 QQ 音乐 `u.y.qq.com/cgi-bin/musicu.fcg` 的 `CgiGetEVkey` 换取该曲目的 ekey；
4. ekey 经 TC-TEA 派生出主密钥，再用 QMCv2 的 RC4 / Map 流密码逐段解密；
5. 校验音频文件头后写入输出目录。

`musicex` 封装本身**不含密钥**，只存了歌曲 mid，因此必须联网换 key —— 这也是为什么单纯编译老版 `qmc-decoder`（只支持 QMCv1）无法处理 `.mflac`。

## 使用

### 直接运行

```bash
cd qqmusic-mflac-decrypt-macos
./convert.sh
```

默认读取：

```
~/Library/Containers/com.tencent.QQMusicMac/Data/Library/Application Support/QQMusicMac/iQmc
```

默认输出到 `~/Desktop/Decoded_Music`。

### 自定义目录

路径**必须加双引号**：

```bash
./convert.sh "加密文件目录" "输出目录"
```

例如：

```bash
./convert.sh "/Users/you/Music/QQ 音乐 下载" "/Users/you/Desktop/我的无损"
```

### 双击运行

双击 `convert.command`。脚本结束时会停在“按回车键关闭窗口”，不会闪退。

### 可选参数

```bash
convert.sh --verbose      # 显示每个文件的封装类型与输出大小
convert.sh --no-clobber   # 不覆盖同名文件，自动加 (1)(2) 后缀
convert.sh -e "<ekey>"    # 手动指定 ekey（离线/接口不可用时）
```

## 环境要求

- macOS
- Python 3.8+（系统自带即可，无需安装）
- QQ 音乐处于**已登录**状态（不必打开播放）

## 常见问题

**提示“登录态已失效”**
重新打开 QQ 音乐，确认账号已登录，再运行一次。登录态存在 plist 里，退出登录会失效。

**提示“该曲目需要 VIP 权限”（result 104003）**
该账号没有这首曲目的下载权限，文件会被跳过，其余文件继续转换。

**转换后文件名带 ` (1)`**
使用了 `--no-clobber` 且输出目录已有同名文件。默认行为是直接覆盖。

**能不能处理别的电脑上的文件？**
可以，但需要那台电脑的 QQ 音乐登录态，或手动用 `-e` 传入该曲目的 ekey。

## 免责声明

本工具仅用于解密**你本人合法下载、拥有使用权**的音频文件，方便在本地设备上正常播放与备份。

请勿用于传播、分发或商业使用受版权保护的内容。使用者需自行承担因不当使用产生的一切后果。

## 致谢

解密算法参考了 [unlock-music](https://github.com/unlock-music/unlock-music) 的开源实现，以及 [ownlight6/qmc-decoder](https://github.com/ownlight6/qmc-decoder) 中“读取本机登录态换取 ekey”的思路。本项目为独立实现，仅依赖 Python 标准库。

## License

[MIT](LICENSE)
