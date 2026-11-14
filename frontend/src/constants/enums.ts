export const Roles={admin:'材料负责人',analyst:'光谱研究员',viewer:'修复阅读者'} as const;
export const RunStatus={queued:'等待解混',running:'计算中',completed:'已完成',failed:'失败'} as const;
export const ReviewStatus={submitted:'待审阅',approved:'已批准',rejected:'退回复配'} as const;
export const BatchStatus={computing:'重算中',ready:'待切换',published:'已切换',failed:'重算失败',superseded:'已被取代'} as const;
export const ItemStatus={pending:'等待重算',running:'重算中',completed:'已完成',failed:'失败'} as const;
