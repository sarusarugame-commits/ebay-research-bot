import re

def adjust_dimensions(dims_str):
    if dims_str == "不明": return "不明"
    nums = re.findall(r"(\d+(\.\d+)?)", dims_str)
    if len(nums) >= 3:
        is_mm = "mm" in dims_str.lower()
        is_inch = "inch" in dims_str.lower() or "in" in dims_str.lower()
        d1 = float(nums[0][0])
        d2 = float(nums[1][0])
        d3 = float(nums[2][0])
        if is_inch:
            d1, d2, d3 = d1 * 25.4, d2 * 25.4, d3 * 25.4
        elif not is_mm: 
            d1, d2, d3 = d1 * 10, d2 * 10, d3 * 10
        d1_final = int(d1 + 20)
        d2_final = int(d2 + 20)
        d3_final = int(d3 + 10)
        return f"{d1_final}x{d2_final}x{d3_final}mm"
    return dims_str

def truncate_weight(weight_str):
    if weight_str == "不明": return "不明"
    nums = re.findall(r"(\d+(\.\d+)?)", weight_str)
    if nums:
        val = float(nums[0][0])
        if "kg" in weight_str.lower() or "キロ" in weight_str:
            val = val * 1000
        val_final = int(val + 100)
        return f"{val_final}g"
    return weight_str

# Test
print(f"Dim (cm): 10x10x5 cm -> {adjust_dimensions('10x10x5 cm')}")
print(f"Dim (mm): 100x100x50 mm -> {adjust_dimensions('100x100x50 mm')}")
print(f"Weight (kg): 1.2kg -> {truncate_weight('1.2kg')}")
print(f"Weight (g): 500g -> {truncate_weight('500g')}")
