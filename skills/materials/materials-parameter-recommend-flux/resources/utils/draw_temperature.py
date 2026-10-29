import json
import matplotlib.pyplot as plt
import numpy as np

# -------------------------- 1. 配置项 --------------------------
input_json = '''
{
    "文献ID": "Not specified",
    "配方ID": "rec_0002",
    "实验目的": "高温热电材料应用与物理性质研究",
    "目标产物信息": {
        "化学式": "REMn2Si2 (RE=Y, Er)",
        "结构原型": "ThCr2Si2-type",
        "是否二维": false,
        "是否半导体": false
    },
    "工艺配方": {
        "生长方法": "Pb flux method",
        "原料": "Y, Er, Mn, Si",
        "助熔剂信息": "99.99% Pb piece... Lead was added to these mixtures at a ratio of 3.8:1 in weight.",
        "容器": "hBN crucible",
        "籽晶": "Not specified",
        "原料摩尔比_原文": "Y or Er, Mn and Si were mixed together at atomic ratios of RE:Mn:Si=0.5–1.5:1.0:1.0.",
        "原料摩尔比_标准化": "RE:Mn:Si = 0.5-1.5:1:1",
        "助熔剂_对_溶质_摩尔比": null,
        "助熔剂家族标签": ["Pb_flux"]
    },
    "温度程序": {
        "是否存在次高温预反应段": "否",
        "升温到次高温时间_h": null,
        "次高温段温度_摄氏": null,
        "次高温段保温时间_h": null,
        "升温到最高温时间_h": 4.42,
        "最高温段保温温度_摄氏": 1350.0,
        "最高温段保温时间_h": 5.0,
        "降温速率_主降温_℃每小时": 50.0,
        "降温时间_主降温_h": 11.0,
        "低温段保温温度_摄氏": null,
        "低温段保温时间_h": null,
        "冷却速率_至室温_标签": "快冷"
    },
    "分离与后处理": {
        "分离方式": "Chemical dissolution",
        "分离温度_摄氏": null,
        "晶体的进一步处理": "removed from the melt using a solution of dilute acetic acid and hydrogen peroxide for 5 days"
    },
    "晶体信息": {
        "晶体形态结构": "thin plates with well-developed (001) faces",
        "最大尺寸_mm": "",
        "形貌标签": "plate_like",
        "是否单晶": true
    }
}
'''
save_path = "figs/temperature_process_curve.png"  # 英文文件名，避免中文路径问题
INIT_TEMP = 25
FAST_COOL_TIME = 0.5
MAIN_COOL_END_TEMP = 800

# -------------------------- 2. 解析JSON并提取温度程序 --------------------------
data = json.loads(input_json)
temp_program = data["温度程序"]
print("✅ Successfully extracted temperature program:")
for k, v in temp_program.items():
    print(f"  {k}: {v}")

# -------------------------- 3. 构建温度-时间数据点 --------------------------
time_points = [0.0]
temp_points = [INIT_TEMP]

# Pre-heating stage (if exists)
if temp_program["是否存在次高温预反应段"] == "是":
    t2_pre = temp_program["升温到次高温时间_h"]
    t2_temp = temp_program["次高温段温度_摄氏"]
    t2_hold = temp_program["次高温段保温时间_h"]
    time_points.append(time_points[-1] + t2_pre)
    temp_points.append(t2_temp)
    time_points.append(time_points[-1] + t2_hold)
    temp_points.append(t2_temp)

# Heat up to max temperature
t1_up = temp_program["升温到最高温时间_h"]
t1_temp = temp_program["最高温段保温温度_摄氏"]
if t1_up is not None and t1_temp is not None:
    time_points.append(time_points[-1] + t1_up)
    temp_points.append(t1_temp)

# Hold at max temperature
t1_hold = temp_program["最高温段保温时间_h"]
if t1_hold is not None:
    time_points.append(time_points[-1] + t1_hold)
    temp_points.append(t1_temp)

# Main cooling stage
t1_cool = temp_program["降温时间_主降温_h"]
if t1_cool is not None:
    time_points.append(time_points[-1] + t1_cool)
    temp_points.append(MAIN_COOL_END_TEMP)

# Fast cool to room temperature
if temp_program["冷却速率_至室温_标签"] == "快冷":
    time_points.append(time_points[-1] + FAST_COOL_TIME)
    temp_points.append(INIT_TEMP)

time_arr = np.array(time_points)
temp_arr = np.array(temp_points)
print(f"\n✅ Successfully built process curve data points:")
print(f"  Time nodes (h): {[round(t, 2) for t in time_arr]}")
print(f"  Temperature nodes (℃): {temp_arr}")

# -------------------------- 4. 绘制温度曲线（纯英文，零字体依赖） --------------------------
# 仅保留负号正常显示，删除所有中文字体配置，用matplotlib默认字体
plt.rcParams['axes.unicode_minus'] = False
# 创建画布
fig, ax = plt.subplots(figsize=(12, 6))

# 绘制核心曲线
ax.plot(time_arr, temp_arr, color="#E63946", linewidth=3, marker="o", markersize=8, 
        markerfacecolor="#457B9D", markeredgecolor="white", markeredgewidth=2)

# 阶段标注样式
annotate_style = dict(xycoords="data", textcoords="offset points", fontsize=11, 
                      bbox=dict(boxstyle="round,pad=0.3", facecolor="#F1FAEE", alpha=0.8),
                      arrowprops=dict(arrowstyle="->", color="#1D3557", lw=1.5))

# 标注各工艺阶段（英文）
ax.annotate(f"Heat to max temp\n{round(t1_up,2)}h → {t1_temp}℃", 
            xy=((t1_up/2), (INIT_TEMP + t1_temp)/2), xytext=(20, 20), **annotate_style)
hold_start = t1_up
hold_mid = hold_start + t1_hold/2
ax.annotate(f"Hold at max temp\n{t1_hold}h @ {t1_temp}℃", 
            xy=(hold_mid, t1_temp), xytext=(20, 0), **annotate_style)
cool_start = hold_start + t1_hold
cool_mid = cool_start + t1_cool/2
ax.annotate(f"Main cooling\n{temp_program['降温速率_主降温_℃每小时']}℃/h → {MAIN_COOL_END_TEMP}℃", 
            xy=(cool_mid, (t1_temp + MAIN_COOL_END_TEMP)/2), xytext=(20, -30), **annotate_style)
fast_cool_start = cool_start + t1_cool
ax.annotate(f"Fast cool to room temp\n{FAST_COOL_TIME}h → {INIT_TEMP}℃", 
            xy=(fast_cool_start + FAST_COOL_TIME/2, (MAIN_COOL_END_TEMP + INIT_TEMP)/2), 
            xytext=(20, -30), **annotate_style)

# 图表标签与样式（纯英文）
ax.set_title(f"Temperature Process Curve (Formula ID: {data['配方ID']})", fontsize=16, pad=20, color="#1D3557")
ax.set_xlabel("Time / h (hours)", fontsize=14, labelpad=10)
ax.set_ylabel("Temperature / ℃ (celsius)", fontsize=14, labelpad=10)
ax.tick_params(axis="both", labelsize=12)
ax.grid(True, linestyle="--", alpha=0.7, color="#CCCCCC")
ax.set_xlim(left=-0.5, right=time_arr[-1] + 1)
ax.set_ylim(bottom=0, top=t1_temp + 50)

# 紧凑布局+保存高清图片
plt.tight_layout()
plt.savefig(save_path, dpi=300, bbox_inches="tight")

# 无桌面环境，注释plt.show()
# plt.show()

print(f"\n🎉 Temperature curve generated successfully! Saved to: {save_path}")