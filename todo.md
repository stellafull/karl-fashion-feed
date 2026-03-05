# Fashion Feed v3 — 优化任务清单

## 后端改进
- [ ] 分类重新规划为4类：RUNWAY_COLLECTION, STREET_STYLE_STYLING, TREND_SUMMARY, BRAND_MARKET_MOVE
- [ ] 修复clustering质量：加强prompt约束，宁可不聚也不能错聚
- [ ] MAX_TOPICS提升到500，过时内容自然淘汰
- [ ] 确保clustering只聚合真正相关的文章

## 前端改进
- [ ] 更新分类标签为新的4类
- [ ] 添加排序功能（按发布时间排序）
- [ ] 添加来源筛选功能（按RSS订阅源筛选）
- [ ] 确保筛选和排序可组合使用

## 交付
- [ ] 运行脚本生成新数据
- [ ] 测试完整流程
- [ ] 推送到GitHub
