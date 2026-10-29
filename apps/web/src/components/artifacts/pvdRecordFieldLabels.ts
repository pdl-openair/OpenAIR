export const PVD_FIELD_LABELS: Record<string, string> = {
  target_system: "靶材体系",
  target_composition: "靶材组成",
  film_composition: "薄膜组成",
  substrate: "衬底",
  electrode: "电极",
  buffer: "缓冲层",
  film_structure: "薄膜结构",
  sputtering_mode: "溅射模式",
  power: "功率",
  power_density: "功率密度",
  n2_flow: "氮气流量",
  ar_flow: "氩气流量",
  gas_ratio: "气体比例",
  reactive_gas_fraction: "反应气体比例",
  total_flow: "总气体流量",
  pressure: "压力",
  reactive_gas_partial_pressure: "反应气体分压",
  target_substrate_distance: "靶基距",
  substrate_temperature: "衬底温度",
  deposition_time: "沉积时间",
  film_thickness: "薄膜厚度",
  substrate_bias: "衬底偏压",
  target_effective_area: "靶材有效面积",
  target_geometry: "靶材几何条件",
  chamber_geometry: "腔室几何条件",
  base_pressure: "本底压力",
  substrate_rotation: "衬底旋转",
  post_treatment: "后处理",
  measurement_method: "测量方法",
  measurement_geometry: "测量几何条件",
  sign_convention: "符号约定",
  correction_state: "校正状态",
};

export function pvdFieldLabel(key: string): string {
  const name = key.replace(/^(?:other|context):/, "");
  return PVD_FIELD_LABELS[name] ?? name;
}
