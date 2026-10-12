# 源项目功能 → NoneBot2 实现 对照表

本文档记录真寻插件 **`zhenxun_plugin_genshin_role_info` v4.3.13** 到
**NoneBot2 插件 `nonebot_plugin_genshin_role_info` v4.3.13** 的逐项迁移对照。

- 源项目：<https://github.com/NovaEvset/zhenxun_plugin_genshin_role_info>
- 审计的源提交：`a3a70b675945210ed1c3db950a747822305e0826`（`main`，`chore: sync miao-plugin updates`）
- 目标仓库：<https://github.com/NovaEvset/Genshin-RoleInfo-Plugin>
- 源项目许可证：AGPL-3.0（已保留于目标仓库）

> 源项目文件行数：`__init__.py` 1098、`mys_sync.py` 401、`utils/*` 1948、
> `data_source/*` 1382、`data_source/damage/*` 2840，合计约 8200 行 Python。
> 本文档的每一项均以**实际读取的源码**为依据，未读取的内容不会标记为已审查。

---

## 一、命令入口

| 源项目功能 | 原实现位置 | 目标实现方式 | 测试方式 | 状态 |
|---|---|---|---|---|
| `原神绑定UID` / `绑定原神`（正则 `(?:原神绑定\|绑定原神).*?(\d+)`，priority 5，block） | `__init__.py:99,354-375` | `on_regex` 同正则；`__init__.py` 中 `_` 处理器返回 `Outcome` | `test_plugin.py::test_all_regex_commands_registered`；`bind_uid/get_uid` 逻辑 | 完成 |
| `原神解绑`（priority 5，block） | `__init__.py:100,378-384` | `on_command("原神解绑")` 同名同优先级 | `test_plugin.py::test_all_commands_registered` | 完成 |
| `角色面板`（正则 `(.*)面板(.*)`，priority 4，**block=False**） | `__init__.py:105,805-825` | `on_regex` 同正则，`block=False` | `test_plugin.py::test_block_flags`；`test_card_rendering.py` 全套渲染 | 完成 |
| `更新原神面板` / `刷新原神面板`（走 `面板` 正则，末尾可带 UID 或 @） | `__init__.py:819-823,953-975` | 同一 `get_card` 处理器内分流到 `update()` | `test_plugin.py`；`update()` 60 秒冷却分支 | 完成 |
| `原神角色排行`（priority 4，block） | `__init__.py:101,901-923` | `on_command` + `get_char()` | `test_plugin.py`；`test_card_rendering.py::test_draw_role_pic_*` | 完成 |
| `最强XX` / `群最强XX`（正则 `^(最强\|群最强)(.*)`，priority 4） | `__init__.py:106,828-856` | `on_regex` 同正则；`_group_role_extreme(best=True)` | 群榜单目录读写逻辑；`test_card_rendering.py` | 完成 |
| `最菜XX` / `群最菜XX`（正则 `^(最菜\|群最菜)(.*)`，priority 4） | `__init__.py:107,859-887` | 同上，`best=False` | 同上 | 完成 |
| `XX评分排行` / `XX评分榜单`（仅群聊，priority 4，block） | `__init__.py:108,686-773` | `on_regex` 同正则 + `permission=GROUP` | `test_plugin.py::test_role_rank_requires_group`；`test_rank_utils.py` 25 项 | 完成 |
| `XX伤害排行N` / `XX伤害榜单N`（1-based 序号） | `__init__.py:108,698-731`；序号解析 `utils/rank_utils.py:19-34` | 同逻辑，`select_damage_metric` 未改动 | `test_rank_utils.py::test_select_damage_metric_*` | 完成 |
| `XX花/羽/沙/杯/冠适配` | `__init__.py:109,511-544` | `on_regex` + `gen_artifact_adapt()` | `test_card_rendering.py::test_draw_artifact_card_uid_mode` | 完成 |
| `XX推荐` / `XX套推荐` / `XX独立套推荐`（末尾可带伤害序号） | `__init__.py:110,547-658` | `on_regex` + `gen_artifact_recommend()` / `gen_suit_recommend()` | `test_recommend.py` 24 项 | 完成 |
| `圣遗物榜单`（别名 `圣遗物排行`，priority 4，block） | `__init__.py:111,776-802` | `on_command(aliases=...)` | `test_plugin.py::test_all_commands_registered` | 完成 |
| `群圣遗物榜单`（别名 `群圣遗物排行`） | `__init__.py:112,661-683` | `on_command(aliases=...)` | 同上；`test_card_rendering.py::test_draw_artifact_card_group_mode` | 完成 |
| `重置最强XX`（超级用户，priority 3，**block=False**） | `__init__.py:113,890-898` | `on_command(permission=SUPERUSER)` | `test_plugin.py::test_block_flags` | 完成 |
| `检查原神面板更新`（超级用户，priority 3，block） | `__init__.py:114,1083-1087` | 同命令；远端地址改为新仓库 | `test_plugin.py` | 完成 |
| `圣遗物导入`（发送操作步骤提示） | `__init__.py:119,387-391` | `on_command` + 相同文案 | `test_plugin.py` | 完成 |
| 私聊发送 `good.json` 导入（priority 1，block=False，仅私聊） | `__init__.py:117,394-508` | `on_message(permission=PRIVATE)`；`get_private_file_url` → `AsyncHttpx.get_content` | `test_plugin.py::test_priorities_match_source` | 完成 |
| `米游社更新原神面板`（priority 3，block） | `__init__.py:121,140-200` | 保留命令；登录态改为**可选的约定式注入**（见第四节） | `test_plugin.py` | 部分完成（见限制） |
| `gsck`（超级用户，源实现为空壳） | `__init__.py:120,203-207` | 保留命令并保持空壳语义（源码仅取 UID 后无操作） | `test_plugin.py` | 完成（保持原状） |

**匹配器总数：17**（11 个 `on_command` + 6 个 `on_regex`），与源项目一致。

---

## 二、业务与渲染逻辑

| 源项目功能 | 原实现位置 | 目标实现方式 | 测试方式 | 状态 |
|---|---|---|---|---|
| 橱窗数据获取（MicroGG 优先、Enka 回退） | `__init__.py:93-98,249-299` | 接口列表来自配置；逐个尝试后回退 | `test_utils.py::test_download_file_tries_candidate_urls`（候选回退语义） | 完成 |
| HTTP 状态码提示映射（400/404/424/429/500/503） | `__init__.py:252-259` | 原样保留于 `_STATUS_HINT` | 真实运行中 MicroGG 返回 424 的分支 | 完成 |
| Enka 响应 → 角色数据模型（属性/天赋/命座/武器/圣遗物解析） | `utils/card_utils.py:202-458` | 逻辑未改动，仅路径改为集中式 | `test_real_uid.py::test_parse_real_enka_response` 等 5 项 | 完成 |
| 圣遗物强化次数解析（Enka `appendPropIdList`） | `utils/card_utils.py:100-111` | 未改动 | `test_real_uid.py::test_artifact_substats_have_expected_fields` | 完成 |
| 圣遗物缓存归一化与去重 | `utils/card_utils.py:144-191` | 未改动 | `test_real_uid.py`；`test_card_rendering.py` | 完成 |
| 旅行者元素识别（荧/空 → X主） | `utils/card_utils.py:194-199` | 未改动 | `test_artifact_score.py::test_get_effective_traveler_by_element` | 完成 |
| 角色别名解析 | `__init__.py:210-216` | `get_role_name()` 未改动 | `test_plugin.py` 正则用例 | 完成 |
| UID 绑定存储（`player_info/qq2uid.json`） | `__init__.py:219-233` | 未改动（源项目本已与真寻解耦） | `test_utils.py` JSON 读写 | 完成 |
| UID 合法性校验（9 位、首位 1-9） | `__init__.py:1037-1038` | `check_uid()` 未改动 | `test_plugin.py` | 完成 |
| 角色面板卡渲染（1080×N、立绘/属性/天赋/命座/武器/圣遗物/伤害表/权重） | `data_source/draw_role_card.py`（404 行） | 未改动，网络图标经 `AsyncHttpx` 等价实现获取 | `test_card_rendering.py` 24 项；真实 UID 实拍验证 | 完成 |
| 伤害表渲染（暴击/期望双列 + Buff 列表） | `data_source/damage/render.py`（273 行） | 未改动；**修复了 `res/` 路径漏级 bug** | `test_card_rendering.py::test_damage_render_uses_bundled_assets` | 完成 |
| 圣遗物卡片渲染 | `data_source/draw_artifact_card.py`（240 行） | 未改动；QQ 头像遮罩用 numpy 合成 | `test_card_rendering.py::test_draw_qq_logo_mask` | 完成 |
| 推荐卡片渲染 | `data_source/draw_recommend_card.py`（400 行） | 未改动 | `test_recommend.py` | 完成 |
| 群排行卡渲染 | `data_source/draw_role_rank_card.py`（239 行） | 未改动 | `test_card_rendering.py::test_draw_role_rank_card*` | 完成 |
| 更新结果图渲染 | `data_source/draw_update_card.py`（268 行） | 未改动 | `test_card_rendering.py::test_draw_role_pic_*` | 完成 |
| 图片预处理与消息段构造（JPEG/PNG 自适应） | `utils/image_utils.py:186-221` | 未改动 | `test_card_rendering.py::test_image_build_*` | 完成 |
| 零宽字形安全绘制兜底 | `utils/image_utils.py:37-100` | 未改动 | `test_card_rendering.py::test_draw_helpers_do_not_crash_with_zero_width_glyphs` | 完成 |
| 群最强/最菜记录与超越判定 | `__init__.py:978-1020` | 未改动 | 群榜单目录读写；`test_card_rendering.py` | 完成 |
| 群圣遗物榜单合并（按评分取前 20） | `__init__.py:1023-1034` | 未改动 | `test_card_rendering.py::test_draw_artifact_card_group_mode` | 完成 |
| 更新冷却（60 秒） | `__init__.py:954-970` | 未改动 | `update()` 分支 | 完成 |
| 每日更新检查（随机 9–22 时） | `__init__.py:1090-1098` | `nonebot-plugin-apscheduler`（可选依赖）；未安装时告警并跳过 | 加载期告警验证 | 完成（可选） |

---

## 三、Miao-Plugin 兼容评分与伤害计算

该子系统（`data_source/damage/` + `utils/artifact_utils.py`）在源项目中
**对真寻框架零耦合**（仅依赖 stdlib + PIL），因此整包移植。

| 源项目功能 | 原实现位置 | 目标实现方式 | 测试方式 | 状态 |
|---|---|---|---|---|
| 规则表装载（129 角色 / 665 detail / 495 buff） | `data_source/damage/rules/__init__.py` | 未改动，仅路径集中化 | `test_damage_engine.py::test_rules_loaded` | 完成 |
| Miao JS 规则 → Python 转译（1079 个 `__function__`） | `data_source/damage/miao_runtime.py`（1090 行） | 未改动 | `test_damage_engine.py::test_parity_across_many_rules` | 完成 |
| 4 个手写特殊规则（心海/优菈/迪奥娜/重云） | `miao_runtime.py:808-890` | 未改动 | 同上（含于 parity 全量对比） | 完成 |
| 伤害计算核心（抗性/防御/暴击/增幅/剧变/月曜/星辉/结晶） | `data_source/damage/calculator.py`（207 行） | 未改动 | `test_damage_engine.py` 计算器单元测试 11 项 | 完成 |
| 反应与等级基数表 | `data_source/damage/reactions.py`（306 行） | 未改动 | `test_damage_engine.py` 反应与插值测试 | 完成 |
| Buff 应用（角色 buff + 武器 + 圣遗物套装） | `data_source/damage/buffs.py`（159 行） | 未改动 | parity 全量对比；真实 UID 伤害验证 | 完成 |
| 面板 → `DamageContext` 适配 | `data_source/damage/adapter.py`（120 行） | 未改动 | `test_damage_engine.py` | 完成 |
| 圣遗物评分（Miao 公式、9 档评级、强化次数） | `utils/artifact_utils.py`（899 行） | 未改动 | `test_artifact_score.py` 54 项 | 完成 |
| 有效词条权重解析（约 40 个角色特例） | `utils/artifact_utils.py:279-858` | 未改动 | `test_artifact_score.py::test_get_effective_*` | 完成 |
| 单件圣遗物排名（按目标伤害） | `data_source/damage/recommend.py:130-145` | 未改动 | `test_recommend.py::test_rank_single_artifacts_*` | 完成 |
| 整套推荐：穷举路径（组合数 ≤ 12000） | `recommend.py:180-190` | 未改动 | `test_recommend.py::test_recommend_artifact_set_exhaustive_path` | 完成 |
| 整套推荐：集束搜索路径（> 12000） | `recommend.py:193-243` | 未改动 | `test_recommend.py::test_recommend_artifact_set_beam_path` | 完成 |
| 伤害目标序号校验（剔除「额外说明」、拒绝文本行） | `recommend.py:88-111` | 未改动 | `test_recommend.py::test_get_damage_target_*` | 完成 |

### 交叉验证（parity）

迁移后实现与**源实现**在以下范围内逐项比对，输出（含字符串与 `is_text` 标记、键顺序）完全一致：

- 胡桃参考面板（3 条伤害 + 额外说明）
- 真实 UID 缓存中的全部 9 个角色
- 遍历全部 129 条规则角色（跳过需元素的旅行者键），实测纳入 100+ 个角色

测试：`tests/test_damage_engine.py::test_parity_*`。

---

## 四、真寻专有耦合点与替换方案

| 耦合点 | 源位置 | 替换方案 | 状态 |
|---|---|---|---|
| `zhenxun.configs.utils.PluginExtraData` / `zhenxun.utils.enum.PluginType` | `__init__.py:26,29`（用于构造 `PluginMetadata.extra`） | 改为 NoneBot2 原生 `PluginMetadata`；作者/版本信息写入 `extra` | 完成 |
| `zhenxun.utils.http_utils.AsyncHttpx` | `utils/image_utils.py:7`、`utils/download_utils.py:48` | 新增 `utils/http_utils.py`，以**相同静态方法签名**（`get_content` / `download_file`）重新实现：共享 `httpx.AsyncClient`、连接/读取超时分离、5xx 与网络异常有限重试、4xx 不重试、流式下载 | 完成 |
| `?` 货币扣费装饰器（`gold_cost`） | `__init__.py:512,548,662,687,777`（源项目中已全部注释停用） | 不引入（源项目已停用，且目标环境无该体系） | 完成（保持源行为） |
| 真寻配置中心 | `__init__.py` 内的硬编码常量 | `config.py`（pydantic）＋ `.env` 注入；接口地址、超时、数据目录、更新检查开关均可配 | 完成 |
| `nonebot_plugin_apscheduler.scheduler` | `__init__.py:24,1090-1098` | 改为**可选依赖**：`try/except` 导入，缺失时打印告警并跳过每日检查，插件其余功能不受影响 | 完成 |
| 跨插件引用 `..mys_sign.data_store.load_user` / `..mys_sign.mys_api.MysAPI` | `__init__.py:142-143` | `mys_sign` 是真寻仓库内的扫码登录插件，非标准 NoneBot2 生态，无法直接复用。改为**约定式注入**：环境变量 `GENSHIN_ROLE_INFO_MYS_PROVIDER=模块:属性` 指向提供 `load_user()` 与 `MysAPI()` 的对象；未配置时该命令返回明确的配置指引。`mys_sync.py` 的数据转换与落盘逻辑（401 行）**完整保留** | **部分完成**（见下方限制） |
| 真寻 `data` 目录 | `utils/card_utils.py:9-28` | 集中式 `paths.py`：内置资源固定在插件包 `res/`；用户数据默认 `user_data/`，可用 `GENSHIN_ROLE_INFO_DATA_DIR` 重定向 | 完成 |
| `nonebot.get_bot()` 取当前 Bot | `__init__.py:844,875,1078` | 群最强/最菜改为强制依赖注入 `Bot`（更符合 NoneBot2 规范）；超级用户通知保留 `nonebot.get_bot()` | 完成 |

### 米游社同步的限制说明（不标记为已完成）

`mys_sync.py` 中**与框架无关的部分已全部迁移并通过测试**：

- `convert_character()` / `save_character_cache()`：米游社角色详情 → 本地缓存格式
- `select_genshin_role()`：多 UID 时按 `is_chosen` 选择当前角色
- `bind_single_genshin_uid()`：UID 绑定
- `sync_single_genshin_uid()`：编排（含验证码回调）

**未能复现的部分**：`..mys_sign` 插件提供的米游社**扫码登录与 Cookie 管理**。
该能力属于真寻插件生态，且需要用户交互式登录，无法在迁移范围内重建。
因此 `米游社更新原神面板` 命令在未配置提供方时不工作，返回配置指引。

未执行真实米游社同步的测试（需要真实米游社账号登录态）。

---

## 五、资源与数据文件

| 资源 | 源位置 | 目标位置 | 状态 |
|---|---|---|---|
| JSON 数据表（10 个，含 129 角色伤害规则 231KB、天赋表 322KB、角色信息 91KB） | `res/json_data/` | `nonebot_plugin_genshin_role_info/res/json_data/` | 已迁移（sha256 与上游逐一相同） |
| 字体（4 个，含 6.9MB `hywh.ttf`） | `res/fonts/` | 同名目录 | 已迁移 |
| 元素背景图（7 张） | `res/background/` | 同名目录 | 已迁移 |
| 卡片素材（遮罩/星级/评级/图标等） | `res/other/`、`res/outline/` | 同名目录 | 已迁移 |
| 区域图（15 张，单张最大 5.5MB） | `res/region/` | 同名目录 | 已迁移 |
| **合计** | | **46 MB / 71 个文件** | 已迁移 |

资源来源与许可：JSON 中的 Miao 规则表在其 `source` 字段标注
`repository: https://github.com/yoimiya-kokomi/miao-plugin`、`license: MIT`
（revision `b01d77483268eb2236876ad0995fab27052c09ad`）。图标素材通过
`enka.network` 在运行时下载并缓存到用户数据目录。

---

## 六、迁移中发现的缺陷修复

| 缺陷 | 位置 | 说明 | 回归测试 |
|---|---|---|---|
| 路径漏级（迁移引入） | `data_source/damage/render.py` | 移植时 `OTHER_PATH`/`FONT_PATH` 指向了插件根而非 `res/`，导致伤害表渲染必然 `FileNotFoundError` | `test_card_rendering.py::test_damage_render_uses_bundled_assets` |
| 多候选下载遇到 4xx 即整体放弃（迁移引入） | `utils/http_utils.py` | 首个候选返回 4xx 时不再尝试后续候选；与「按顺序回退接口」的语义不符 | `test_utils.py::test_download_file_tries_candidate_urls` |
| 5xx 不重试（迁移引入） | `utils/http_utils.py` | `_is_retryable` 未覆盖 5xx，重试策略失效 | `test_utils.py::test_get_content_raises_on_500` |
| 排行命令阻塞事件循环（**源项目既有**） | 源 `__init__.py:713,750` | `collect_damage_titles` / `collect_role_rank_entries` 对每个群成员同步调用伤害引擎（实测约 10ms/次），无 `to_thread`；推荐路径有 `to_thread` 但排行没有 | `test_damage_engine.py`；修复为 `asyncio.to_thread` |
| `print()` 输出（**源项目既有**） | 源 `damage/engine.py:58,70`、`draw_role_card.py:63`、`card_utils.py:480` | 失败信息只写 stdout，线上难以排查 | 改为标准库 `logging`（伤害包保持仅依赖 stdlib，不引入 nonebot） |

---

## 七、未迁移 / 明确不迁移的内容

| 内容 | 原因 |
|---|---|
| 真寻插件注册机制（`__zx_plugin_name__`、`PluginType`、插件管理器） | 目标框架为 NoneBot2，已有原生等价机制 |
| 真寻配置中心与金币/经济系统 | 源项目已停用金币扣费；目标环境无该体系 |
| `mys_sign` 扫码登录与米游社 Cookie 管理 | 非标准 NoneBot2 生态，且需要交互式登录；改为可选注入（见第四节） |
| 源项目 `conver.py`（资源转换脚本，已在 `.gitignore` 中） | 属开发期工具，与运行时功能无关 |
| 目标运行的实时验证（真实 QQ + NapCat 连接） | 当前环境无 QQ 账号与 NapCat 实例，见 `docs/TEST_REPORT.md` |
