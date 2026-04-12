def check_first_photo_bg(photo):
    # Set threshold for near-white backgrounds
    WHITE_V_MIN = 205

    # Adjust max_shadow_std formula to use 0.7 multiplier instead of 0.28
    max_shadow_std = (photo.max_shadow * 0.7)
    # New mapping: 0->2.0, 100->72.0
    mapped_value = max(2.0, min(72.0, max_shadow_std))

    # ... rest of the function unchanged
