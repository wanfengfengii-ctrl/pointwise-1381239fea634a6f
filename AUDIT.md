# 逐输出聚合贡献证据

README.md是已有V1业务、查询语言和错误契约；本文件定义新增能力。audit_cases.py仅提供有限手算夹具、公开核账及普通SQLite中断夹具，不实现审计求值或存储。保留全部原文件的业务接口和原测试。

## 共同结果与接口

质控员须能在离开执行现场后，分别核对相同投影行的来源、每次相关聚合实际接纳的校准及贡献，并查看导致候选被筛掉的嵌套调用。只保存站点、计划节点或源身份集合不能表示重复的输出和调用。

新增Python入口：
- labpipe.workflow.processing.execute_audited(storage, plan_id)返回新成果ID。消费原持久计划及其固定输入，业务行和顺序与execute完全相同；每次成功可产生新成果。
- labpipe.results.reader.read_audit(storage, result_id)返回read的全部六个字段，另加audit列表；read及原export对审计成果仍返回原六字段。
- labpipe.results.export.export_audit(storage, result_id, path=None)返回上述完整文档的UTF-8 JSON文本；提供path时同时写出文本，文件I/O异常沿用原规则。

执行侧须传递投影前源身份与调用归属，成果侧须消费同一完整证据并与业务行共同发布；只有执行日志或事后查询当前源均不能交付固定成果。允许新增内部模块或复用声明绑定，无需更改查询语言和编译表示。

## 公开逻辑表示

以下对象均为独立JSON值，整数精确保存，不经过浮点或SQLite整数列截断。字段集合如下；内部可复制树、共享节点或使用事件记录，读回必须展开为该逻辑树。节点路径相对于持久计划中的原query，根为[]，对象下行逐段使用键名，如["columns","sum","query","where","left"]；不使用别名或进程内地址作为节点身份。

Source = {"revision_id":整数,"table":字符串,"batch_id":字符串,"row_id":字符串}。修订取固定输入中该表的修订，身份包含其余三个字段。当前语言的关系只有scan/filter/project，故每个关系发生次数恰有一个驱动scan来源；聚合成员的来源独立记录，不能混入驱动来源。

audit按保存输出序位排列，每项为：
{"ordinal":从0起连续序号,"source":Source,"calls":[Call,...]}

Call为：
{"node":节点路径,"op":"count或sum","result":整数或null,
 "candidates":[Candidate,...],"members":[Member,...]}

Candidate为：
{"ordinal":从0起连续序号,"source":Source,"accepted":布尔,"calls":[Call,...]}

Member为：
{"candidate":候选序号,"value":整数或null,"calls":[Call,...]}

每次Call的candidates按该子关系驱动scan的原顺序列出全部候选；accepted表示经过全部filter/project后是否成为最终关系成员。members恰好引用accepted=true的候选，保持原顺序；不能因业务值相同合并身份或发生次数。count的每个Member.value为1且Member.calls为空；sum的Member.value保存其value表达式的实际结果，null保留但不贡献总和。无非null贡献时sum.result为null；有贡献且相消时为整数0。

调用上下文由树的位置完整确定：输出的calls收集生成该保留输出时沿顶层关系链直接求值的聚合；Candidate.calls收集处理该scan候选、经过其filter/project时直接求值的聚合，包括最终被拒候选；Member.calls只收集sum的value表达式直接触发的聚合。被子调用拥有的调用放在子调用内部，不能再平铺进父列表。即使同一计划节点和值相同，每个上下文仍各有一次完整调用对象。

calls按V1实际求值顺序排列：先处理关系input；投影按columns声明顺序，eq先left后right。聚合先求子关系；sum仅为接纳成员求value。被先前筛选拒绝的候选不继续求后续表达式；不为补证据执行原本未触发的调用。顶层最终被拒行无输出证据；子聚合中的被拒候选及其已发生调用仍保留。投影隐藏别名、连续投影及后续筛选不丢失此前已发生的证据。来源和值相同也不能跨输出共享可变调用列表。

## 手算与入口

audit_cases.py中的fixtures、report_query、wrapped_query、rejection_query、member_value_query是完整可运行输入构造器。五个观测身份o1至o5依次为A、A、B、C、D；校准c1/c2/cn为A的3/3/null，cc为C的null，d1/d2为D的3/-3。report_query隐藏观测ID，两条A输出均为{"station":"A","count":3,"sum":6}，但source分别是o1、o2。B为0/null，C为1/null，D为2/0。两次A的count分别有三个成员，各贡献1；sum分别有3、3、null三个成员，来源均不得按值合并。

rejection_query的外层count检查每个校准站点有几个观测，只接纳内层count=2者。每个输出的外层count=3；六个候选均触发同一内层节点，结果依次为2、2、2、1、1、1，后三个被拒，内层证据仍须保留。member_value_query把同一内层count用作sum的value，六个成员贡献为2、2、2、1、1、1，总和9；这些内层调用属于Member.calls而不是Candidate.calls。原tests.test_plans.nested_query及合法别名改名也应保持业务值及各自上下文。

最小单成员sum示例：若固定观测修订1只有(day,o1)，校准修订2只有(bench,cn,value=null)，且唯一输出列为sum，则audit为：
```json
[{"ordinal":0,"source":{"revision_id":1,"table":"observations","batch_id":"day","row_id":"o1"},"calls":[{"node":["columns","sum"],"op":"sum","result":null,"candidates":[{"ordinal":0,"source":{"revision_id":2,"table":"calibrations","batch_id":"bench","row_id":"cn"},"accepted":true,"calls":[]}],"members":[{"candidate":0,"value":null,"calls":[]}]}]}]
```

## 发布、读取与兼容

业务行与完整证据同事务发布，执行或证据写入失败不留下本次成果。保留原异常分类和既有完整成果。复用现有事务即可，不要求新恢复协议。read_audit和export_audit只消费保存内容，不重新加工或扫描源；更正源、修改返回对象、关闭重开均不得改变已有证据。未知或未审计的旧成果请求审计读取时抛ResultError；审计输出为空时仍是有效审计成果，audit=[]。读回须拒绝证据缺失或输出序位不完整的成果。

沿用同版本V1库可直接打开，旧成果不回填；允许新增表或列，保留原数据和接口。内部存储组织自行选择，不能将证据仅留在进程缓存中。不新增查询算子、索引、压缩、增量、并发或资源优化；允许全量加工、逐输出复制完整证据。

## 联合核验与交付

以下普通基线命令不依赖新增实现：
```sh
python3.11 -B -m unittest discover -s tests -t . -v
python3.11 -B -m labpipe.workflow.processing demo
python3.11 -B audit_cases.py v1
```

实现后运行python3.11 -B audit_cases.py check核对手算、连续投影、筛选拒绝调用、sum成员调用、遮蔽改名、空成果、返回隔离及真实磁盘重开导出。该核验仅是公开小例，不代替全部契约。

使用audit_cases.abort_writes在实际保存证据的表上安装真实SQLite触发器，令证据写入抛sqlite3.IntegrityError；核对全部业务表行数不增、旧成果可完整读取。若证据使用独立表，运行check --evidence-table 实际表名；若证据以头部UPDATE保存，使用check --evidence-table 实际表名 --evidence-event UPDATE。目标须是本实现实际写入证据的位置，不能选未触发的表；可扩展该普通夹具，覆盖选择的存储方式，不固定新表名。

还须独立核对同row_id跨批次身份、超过64位的贡献、filter在project之后、嵌套退出及空子关系；这些均沿用原业务规则。执行异常、证据写入失败与成功发布后的读取导出须贯通验证，保留全部原回归及非零失败退出码。提交简短的来源、候选/成员归属与发布交接不变量说明。

更新已有.pointwise-runtime.json，使verify同时运行原回归和新证据核账，demo展示真实审计成果及重开导出；保留其version=1、kind=cli、setup参数数组列表及verify/demo参数数组约定。可用包装入口串联检查，不增加安装依赖。
