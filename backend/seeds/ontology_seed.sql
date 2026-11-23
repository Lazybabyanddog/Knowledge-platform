-- =============================================================================
-- 文件：backend/seeds/ontology_seed.sql
-- 用途：导入本平台在用的本体基线数据 —— 5 个实体类 + 12 个关系类型
-- 适用：PostgreSQL 16（pgvector 镜像同库），表结构版本 >= a7c4e2d91f60
--       （该迁移把 ontology_*.status 从 varchar 改为 smallint，见 alembic/versions）
-- 前置：1) 已执行 alembic upgrade head
--       2) 已存在 slug = 'default' 的工作空间；否则改脚本中的 slug 或先建空间
--       3) 可选：app_user 中存在 username = 'admin'，用于回填 created_by
-- 幂等：以唯一约束 (workspace_id, code) 为冲突键做 upsert，可反复执行；
--       已存在的行只更新 name/description/attributes/status/updated_at，
--       不动 id、created_by、created_at、version
-- 执行：psql "postgresql://ai_platform:ai_platform@localhost:5434/ai_platform" \
--            -v ON_ERROR_STOP=1 -f backend/seeds/ontology_seed.sql
-- 说明：本脚本直接写表，不经 service 层，因此不产生 audit_log 记录，
--       也不会触发“每次修改 version + 1”的业务规则；需要留痕或版本推进时请走接口。
-- =============================================================================
--
-- 字段语义（与 app/domain/enums.py、alembic a7c4e2d91f60 一致）：
--   status      smallint  0 = 有效（参与抽取约束与图谱投影）
--                         1 = 失效（保留历史数据，不参与抽取/投影）
--             表上有 CHECK (status IN (0,1))，写别的值会直接约束报错
--   code        大小写敏感的字符串，^[A-Za-z][A-Za-z0-9_]{1,63}$，
--              有效/失效定义之间 code 仍不可重复（唯一键不含 status）
--   attributes  JSONB，键为属性编码，值为 {"type": ..., "description": ...}，
--              数组类型额外带 "items"；空对象 {} 表示不约束字段
--   parent_id   父类 id；本基线全为顶层类（NULL）。若需要层级，
--              在类导入后用 UPDATE ... SET parent_id = (SELECT id ... ) 补齐
--   version     种子统一从 1 开始，不复制来源库里的历史版本号
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 0. 前置校验：工作空间必须存在，否则直接报错，避免插出一片 workspace_id 为空的脏数据
-- -----------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM workspace WHERE slug = 'default') THEN
        RAISE EXCEPTION '未找到 slug = ''default'' 的工作空间：请先创建工作空间，或修改本脚本中的 slug';
    END IF;
END
$$;

-- -----------------------------------------------------------------------------
-- 1. 实体类（ontology_class）
--    5 条顶层类，align 到“法律案例抽取”场景；attributes 即抽取时的字段约束
-- -----------------------------------------------------------------------------
WITH ws AS (
    SELECT id FROM workspace WHERE slug = 'default'
),
seed (code, name, description, attributes, status) AS (
    VALUES
    -- 指导性案例：一个案例一行，属性覆盖标题、案号、关键词、案由、发布时间、裁判要点
    (
        'lw_001',
        '指导性案例',
        '最高人民法院发布的具有指导意义的典型案例，包含案例编号、名称、案由、裁判要点、裁判结果等信息',
        $json${
          "title":           {"type": "string", "description": "案例标题"},
          "case_no":         {"type": "string", "description": "指导案例编号"},
          "keywords":        {"type": "array", "items": {"type": "string"}, "description": "案例关键词"},
          "case_cause":      {"type": "string", "description": "案件案由"},
          "publish_date":    {"type": "date",   "description": "案例发布时间"},
          "judgment_point":  {"type": "string", "description": "案例裁判要点"}
        }$json$::jsonb,
        0
    ),
    -- 组织：企业、机构（含中介机构、被告单位等）
    (
        'lw_002',
        '组织',
        '案例中出现的企业、机构等组织主体',
        $json${
          "name":     {"type": "string", "description": "组织名称"},
          "org_type": {"type": "string", "description": "组织类型"}
        }$json$::jsonb,
        0
    ),
    -- 自然人：姓名可能是原文匿名化称谓，故 description 里点明
    (
        'lw_003',
        '自然人',
        '法律案例中出现的自然人主体，包括原告、被告、房屋产权人及其他相关人员',
        $json${
          "name": {"type": "string", "description": "自然人姓名或原文中的匿名化称谓"}
        }$json$::jsonb,
        0
    ),
    -- 房屋：交易标的，地址保留原文表述，不做规范化
    (
        'lw_004',
        '房屋',
        '法律案例中涉及的房屋、不动产等交易标的',
        $json${
          "address":       {"type": "string", "description": "房屋地址，保留原文表述"},
          "property_type": {"type": "string", "description": "房屋类型"}
        }$json$::jsonb,
        0
    ),
    -- 法律条文：被案例引用的具体条款
    (
        'lw_005',
        '法律条文',
        '法律、法规、司法解释等规范性文件中的具体法律条款',
        $json${
          "content":    {"type": "string", "description": "法律条文原文"},
          "law_name":   {"type": "string", "description": "法律法规名称"},
          "article_no": {"type": "string", "description": "具体条文编号"}
        }$json$::jsonb,
        0
    )
)
INSERT INTO ontology_class (
    id, workspace_id, code, name, description, attributes,
    version, status, created_by, created_at, updated_at
)
SELECT
    gen_random_uuid(),
    ws.id,
    s.code,
    s.name,
    s.description,
    s.attributes,
    1,                                   -- 种子数据从版本 1 开始
    s.status,
    (SELECT id FROM app_user WHERE username = 'admin' LIMIT 1),  -- 找不到则留空
    now(),
    now()
FROM seed s
CROSS JOIN ws
ON CONFLICT ON CONSTRAINT uq_ontology_class_code DO UPDATE
SET name        = EXCLUDED.name,
    description = EXCLUDED.description,
    attributes  = EXCLUDED.attributes,
    status      = EXCLUDED.status,
    updated_at  = now();

-- -----------------------------------------------------------------------------
-- 2. 关系类型（ontology_relation）
--    起点/终点按 code 反查类 id，避免硬编码 UUID 在别的环境里对不上
--    注意：允许起点=终点（SIGNS_CONTRACT 是自然人之间的合同，两端都是 lw_003）
-- -----------------------------------------------------------------------------
WITH ws AS (
    SELECT id FROM workspace WHERE slug = 'default'
),
seed (code, name, src_code, tgt_code, description, status) AS (
    VALUES
    -- 案例 -> 法条
    ('CITES_LAW',         '引用法条',     'lw_001', 'lw_005',
     '表示该指导性案例在裁判过程中引用或涉及某项法律条文', 0),
    -- 案例 -> 当事人；原文未写描述的两条保持 NULL
    ('HAS_DEFENDANT',     '被告',         'lw_001', 'lw_003', NULL, 0),
    ('HAS_PLAINTIFF',     '原告',         'lw_001', 'lw_002',
     '表示某组织在该指导性案例中作为原告提起诉讼', 0),
    -- 案例 -> 涉案要素
    ('INVOLVES_ORG',      '涉及组织',     'lw_001', 'lw_002', NULL, 0),
    ('INVOLVES_PERSON',   '涉及人员',     'lw_001', 'lw_003', NULL, 0),
    ('INVOLVES_PROPERTY', '涉及房屋',     'lw_001', 'lw_004', NULL, 0),
    -- 房屋交易链路：挂盘 -> 带看 -> 购买 -> 付佣
    ('LISTS_PROPERTY',    '挂牌出售房屋', 'lw_003', 'lw_004',
     '自然人作为房屋所有权人或出售委托人，通过房地产中介机构发布房屋出售委托、挂牌信息或委托出售某处房屋。只有原文明确记载自然人实施了挂牌出售或委托出售行为，才能建立该关系。不能仅凭自然人与房屋存在产权关系就推断其存在挂牌出售行为。', 0),
    ('SHOWS_PROPERTY',    '带看房屋',     'lw_002', 'lw_004',
     '组织作为房地产中介机构，带领客户实地查看某处房屋，或者安排客户进行房屋现场看房。只有原文明确记载该组织实施了房屋带看行为，才能建立该关系。仅向客户提供房屋地址、图片或房源信息，不应直接认定为带看房屋。', 0),
    ('PURCHASES',         '购买房屋',     'lw_003', 'lw_004',
     '自然人作为购房人，通过签订房屋买卖合同、完成房屋交易等行为购买某处房屋。只有原文明确记载自然人已经购买或签订合同购买该房屋，才能建立该关系。仅表达购买意向、看房或询问房屋价格，不应认定为购买关系。', 0),
    ('PAYS_COMMISSION',   '支付佣金',     'lw_003', 'lw_002',
     '表示自然人向提供居间服务的房地产中介机构支付房屋交易佣金或居间服务报酬。只有原文明确记载佣金已经支付，才能建立该关系。仅约定支付佣金、产生佣金支付义务或存在佣金争议，不应直接认定已经完成支付。', 0),
    -- 中介 -> 客户
    ('PROVIDES_SERVICE',  '提供居间服务', 'lw_002', 'lw_003',
     '组织作为房地产中介机构，为自然人提供房屋交易相关的居间服务，包括房源推荐、房屋带看、交易信息沟通、价格协商等行为。只有原文明确记载组织向该自然人提供了相关居间服务，才能建立该关系。不能仅因组织和自然人同时出现在同一案件中就建立关系。', 0),
    -- 自然人 -> 自然人
    ('SIGNS_CONTRACT',    '签订合同',     'lw_003', 'lw_003',
     '表示两名自然人之间存在合同签订行为，起点为合同一方，终点为合同另一方。只有原文明确记载双方签订了合同，才能建立该关系。仅存在协商、磋商或合同签订意向，不应认定为已经签订合同。', 0)
),
resolved AS (
    SELECT
        s.code,
        s.name,
        s.description,
        s.status,
        ws.id AS workspace_id,
        sc.id AS source_class_id,
        tc.id AS target_class_id
    FROM seed s
    CROSS JOIN ws
    LEFT JOIN ontology_class sc ON sc.workspace_id = ws.id AND sc.code = s.src_code
    LEFT JOIN ontology_class tc ON tc.workspace_id = ws.id AND tc.code = s.tgt_code
)
INSERT INTO ontology_relation (
    id, workspace_id, code, name, description,
    source_class_id, target_class_id,
    version, status, created_by, created_at, updated_at
)
SELECT
    gen_random_uuid(),
    r.workspace_id,
    r.code,
    r.name,
    r.description,
    r.source_class_id,
    r.target_class_id,
    1,
    r.status,
    (SELECT id FROM app_user WHERE username = 'admin' LIMIT 1),
    now(),
    now()
FROM resolved r
ON CONFLICT ON CONSTRAINT uq_ontology_relation_code DO UPDATE
SET name              = EXCLUDED.name,
    description       = EXCLUDED.description,
    source_class_id   = EXCLUDED.source_class_id,
    target_class_id   = EXCLUDED.target_class_id,
    status            = EXCLUDED.status,
    updated_at        = now();

-- -----------------------------------------------------------------------------
-- 3. 校验输出：期望 classes = 5、relations = 12，且 invalid 计数为 0
-- -----------------------------------------------------------------------------
SELECT 'ontology_class' AS table_name,
       count(*)                                            AS rows,
       count(*) FILTER (WHERE status = 1)                  AS invalid_rows
FROM ontology_class
UNION ALL
SELECT 'ontology_relation',
       count(*),
       count(*) FILTER (WHERE status = 1)
FROM ontology_relation;

-- 关系两端是否都解析成功（期望 0 行）
SELECT r.code, r.source_class_id, r.target_class_id
FROM ontology_relation r
LEFT JOIN ontology_class s ON s.id = r.source_class_id
LEFT JOIN ontology_class t ON t.id = r.target_class_id
WHERE s.id IS NULL OR t.id IS NULL;

COMMIT;

-- =============================================================================
-- 4. 回滚模板（按需取消注释执行）：先删关系再删类，避免外键 RESTRICT 报错
-- =============================================================================
-- BEGIN;
-- DELETE FROM ontology_relation
--  WHERE code IN ('CITES_LAW', 'HAS_DEFENDANT', 'HAS_PLAINTIFF', 'INVOLVES_ORG',
--                 'INVOLVES_PERSON', 'INVOLVES_PROPERTY', 'LISTS_PROPERTY',
--                 'PAYS_COMMISSION', 'PROVIDES_SERVICE', 'PURCHASES',
--                 'SHOWS_PROPERTY', 'SIGNS_CONTRACT');
-- DELETE FROM ontology_class
--  WHERE code IN ('lw_001', 'lw_002', 'lw_003', 'lw_004', 'lw_005');
-- COMMIT;
