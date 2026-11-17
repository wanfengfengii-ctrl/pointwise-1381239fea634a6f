# Labpipe 环境实验室观测加工

Python 3.11 标准库与 SQLite 离线系统，无安装依赖。在项目根目录运行：

```sh
python3.11 -B -m unittest discover -s tests -t . -v
python3.11 -B -m labpipe.workflow.processing demo
```

demo在测试临时目录接纳examples.json、实际编译执行、保存成果、更正校准、关闭重开并导出，断言通过后打印POINTWISE_SCENARIO JSON。需要保留数据库与quality.json时使用 `demo --directory ./daily-run`。样例导入是显式操作；普通打开数据库不导入样例、不重置用户数据。同版本重复打开复用存储，不兼容版本报sqlite3.DatabaseError。

## 实验室的一天

客户是负责多站点仪器质控的环境实验室，上游为仪器观测文件和校准台账。工程师处理输入，质控员使用固定成果调查缺校准、重复观测及异常总量：

1. 接收带独立row_id的观测批次，完整验证后形成观测修订。
2. 接收校准批次，形成独立的校准修订。
3. 对拒绝批次修正上游；同批合法更正生成完整新修订，历史仍可读取。
4. 选择两表修订并freeze，形成不可变固定输入。
5. 登记结构化scan/filter/project及相关count/sum口径。
6. compile保存词法引用归属、类型、格式版本和固定输入。
7. execute逐观测核算同站校准数量与总量，完整成功才事务发布。
8. 关闭重开后read/export固定成果，质控员离线核账，不重新执行。

A站有两条不同ID的相同观测，校准3和5；B站一条观测，无校准。应输出三行：每条A观测count=2、sum=8，B为0/null。不同身份的重复观测与投影后的相同行均保留。

## Python公开接口

所有函数的第一个参数都是同一个Storage实例，ID为数据库内整数。各模块是Python命名空间包，无需安装。

```python
import json
from pathlib import Path
from labpipe.catalog.storage import Storage
from labpipe.workflow.intake import ingest
from labpipe.catalog.snapshots import freeze
from labpipe.planning.compiler import compile
from labpipe.workflow.processing import execute
from labpipe.results.reader import read
from labpipe.results.export import export

sample = json.loads(Path('examples.json').read_text(encoding='utf-8'))
with Storage.open('quality.sqlite') as storage:
    o = ingest(storage, 'observations', sample['observations']['batch_id'],
               sample['observations']['rows'])
    c = ingest(storage, 'calibrations', sample['calibrations']['batch_id'],
               sample['calibrations']['rows'])
    fixed = freeze(storage, o['revision_id'], c['revision_id'])
    plan = compile(storage, fixed, sample['query'])
    result = execute(storage, plan)
    print(read(storage, result))
    export(storage, result, 'quality.json')
```

ingest返回table、batch_id、revision_id、row_count。同一当前批次的规范有序行完全相同则返回原接纳回执，不追加修订；即使其他批次已推进当前修订，也返回该批次原接纳关系。需要最新表版本时调用RevisionStore.current_revision(table)。同批更正完整替换该批次，其他批次保持；空批次可清空该批次。源身份是(table,batch_id,row_id)，批内row_id唯一。输入行只含row_id、station、value；前二者必须是非空白字符串，value仅整数或null，拒绝bool、浮点及额外字段。上游先将两个表统一到单位U，不隐式换算。批次排序按batch_id字符串，批内按提交顺序；行值和顺序共同决定重放。

RevisionStore.read_revision(id)返回带batch_id的历史行；返回对象和输入对象均与保存数据隔离。freeze要求对应表的有效修订，保存两表模式与单位。compile深拷贝并立即持久化查询，不保留调用方对象或活动行。execute可多次发布新成果ID，旧成果不变。process_day(storage, observation_revision, calibration_revision, query_tree)串联freeze/compile/execute并返回三个ID；若后阶段失败，已完成的固定输入或计划仍可重用，但不产生本次成果。

read返回result_id、plan_id、snapshot_id、row_order、rows、provenance；来源含固定两表修订、单位和行数。export返回UTF-8 JSON文本，传path时同时写文件。行序是实际保存的0起连续序号。读取与导出只消费已保存成果及来源，不重新编译或执行，也不查当前源指针。

### 审计成果

在原有接口之上新增三个入口（格式与契约见AUDIT.md，归属不变量见INVARIANTS.md）：

```python
from labpipe.workflow.processing import execute_audited
from labpipe.results.reader import read_audit
from labpipe.results.export import export_audit

audited_id = execute_audited(storage, plan)      # 业务行与execute完全相同
document = read_audit(storage, audited_id)       # read的六字段 + audit
text = export_audit(storage, audited_id)         # 完整文档的UTF-8 JSON（可选path）
```

每次输出项保留投影前驱动scan的来源，每次count/sum调用保留候选、接纳成员及精确贡献，递归区分筛选候选与sum成员触发的调用；被拒候选的已发生调用仍保留。证据与业务行同事务发布，失败无部分成果；旧成果无证据，审计读取抛ResultError。业务行仍可用read/export按原六字段读取。

## 查询树契约

examples.json给出完整主样例。对象的op及所列字段为精确字段集合，不接受未知字段：

- scan：op、table、as。table为observations或calibrations，列为batch_id、row_id、station（字符串）及value（可空整数）。
- filter：op、input、where；仅保留等值谓词为真的行。
- project：op、input、as、columns；columns为非空的“输出名→表达式”对象。投影产生新别名，隐藏输入别名；别名必须非空。
- lit：op、value，value限整数、字符串、null。
- col：op、name，可选alias、up。
- eq：op、left、right。同类标量比较；遇null为未知，不通过filter。谓词不能投影成业务值。
- count：op、query。计关系行，包含value=null。
- sum：op、query、value。value是子关系输出环境中的整数或可空整数表达式。忽略null；空关系及全null关系返回null，和为0则返回整数0。

每个filter谓词、project表达式及sum的value在对应输入关系别名的词法帧中解释，并捕获调用处的外层帧。col默认up=0，从当前帧向外查找alias；up先跳过指定数量词法帧，再向外查找。同名声明遮蔽外层，仅该作用域受影响。未限定alias时，在跳过后的可见别名中查找唯一列；同名别名只保留最近声明，不同可见别名均有该列时拒绝歧义。up必须非负整数且小于活动词法深度，不能是bool。跨层同名引用必须明确up；合法别名改名须同步修改所属引用。

例如外层观测别名s、中层观测别名m、最内层校准再次声明s时，最内层谓词以 `{"op":"col","alias":"s","name":"station","up":2}` 读取最外层站点。tests/test_plans.py的nested_query提供完整两层实例和合法改名对照。编译后的引用是声明ID与列名，不在运行时按别名猜测。

相关子查询逐外层行求值，空聚合也是一个标量，不能删除外层行；嵌套返回后继续读取原外层环境。整数使用Python精确整数及JSON文本存储，不经浮点或SQLite整数值列中转。2^70、3、null的count为3，sum为1180591620717411303427。无join、SQL解析、排序、优化器或并行调度。

## 错误与失败边界

BatchError拒绝非法批次，整批不写源行、修订或回执；上游迭代异常保持原分类。SnapshotError拒绝未知或错表修订，不留下固定输入。PlanError拒绝非法引用、类型、算子和执行表示，不留下可执行半计划。未知成果抛ResultError。SQLite或执行异常原分类向Python调用方传播。

成果头、顺序行和来源同一事务提交；行或来源写入失败全部回滚，既有成果不受影响。完整执行先于发布，不暴露运行中成果ID。read也核对来源和行数，拒绝不完整成果。导出的文件不是数据库事务的一部分；文件I/O失败直接传播。

CLI复用相同API，成功打印JSON并退出0，领域、SQLite或输入文件错误打印error及message并退出2。参数使用 `python3.11 -B -m labpipe.workflow.processing --db PATH COMMAND ...`；子命令为ingest TABLE BATCH_ID ROWS_FILE、freeze OBS_REV CAL_REV、compile SNAPSHOT_ID QUERY_FILE、execute PLAN_ID、read RESULT_ID、export RESULT_ID OUTPUT_FILE。--help显示实际参数。程序不创建数据库父目录。

## 回归映射

test_intake覆盖V1/V2：重复身份、整批拒绝、重放、更正历史、冻结和上游异常。test_plans覆盖V4/V5：遮蔽及合法改名、持久引用、歧义/越界/类型与算子拒绝、发布前预检。test_processing覆盖V3/V6：手算核对、空聚合、投影多重集、null、精确大整数、输入顺序与嵌套退出。test_results覆盖V7/V8：真实SQLite触发器分别中断行和来源写入、旧成果完整、固定计划及更正后重开、导出与读取隔离。test_audit覆盖新增审计：业务行与原执行一致、投影重复行来源独立、候选与成员归属、被拒候选嵌套调用、空成果与旧成果拒绝、证据写入失败原子性、重开导出及V1库升级。test_boundaries补充重复初始化、存储版本拒绝、修订/计划写入失败、执行输入不匹配及日加工入口。

AUDIT.md另增三条普通基线命令：`python3.11 -B audit_cases.py v1`校验原手算夹具，`python3.11 -B audit_cases.py check`核对审计手算、投影重复、筛选拒绝、sum成员调用、遮蔽改名、空成果、返回隔离与磁盘重开导出；`check --evidence-table lp_result_audit`再用真实触发器中断证据写入。`.pointwise-runtime.json`的verify串起原回归、`v1_check`与带证据中断的`audited_check`，demo打印审计成果并在重开后导出evidence.json。完整发现命令保留全部原断言。
