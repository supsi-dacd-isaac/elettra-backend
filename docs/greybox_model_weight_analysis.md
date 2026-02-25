# Greybox Model Weight Analysis

## Purpose

This document analyzes how the current greybox mechanical model computes bus mass,
compares it with the actual bus specifications we want to use in production,
and identifies gaps and future improvements.

## Current Greybox Mass Formula

**File:** `simulation/greybox_models.py`, `MechanicalGreyBox` class (line 38)

The model computes mass as:

```
m = battery_pack_density * bus_battery_kwh + k1 * bus_length_m + k2
```

Where:
- `battery_pack_density` = 6.85 kg/kWh (stored in model metadata, fixed at training time)
- `k1` = 1014.0 (fitted parameter, approximates weight-per-meter of bus body)
- `k2` = 2525.0 (fitted constant, base weight)
- `bus_battery_kwh` = battery capacity in kWh (from feature input)
- `bus_length_m` = bus length in meters (from feature input)

This mass feeds into the mechanical energy equation:

```
E_mech = alpha_roll  * m * L
       + alpha_aero  * L * v^2 * (driving_time / total_duration)
       + alpha_up    * m * h_up
       + alpha_down  * m * h_down
```

## Target Mass Formula (from bus model specs)

The production system has actual bus specifications:

```
m = empty_weight_kg + num_battery_packs * battery_pack_weight_kg + max_passengers * (occupancy_percent / 100) * 70
```

Where (example for AA_NF 18m articulated bus):
- `empty_weight_kg` = 18000 (actual curb weight without batteries or passengers)
- `battery_pack_weight_kg` = 253 (weight of one battery pack)
- `battery_pack_size_kwh` = 37 (capacity of one battery pack)
- `num_battery_packs` = 10-14 (configurable, determines total battery capacity)
- `max_passengers` = 120
- `occupancy_percent` = 0-100% (scenario parameter)

## Gap Analysis

### 1. Battery Weight: Close but approximate

| What | Model uses | Actual |
|---|---|---|
| Battery weight | `6.85 * bus_battery_kwh` | `num_packs * 253` |
| For 14 packs (518 kWh) | 6.85 * 518 = 3548 kg | 14 * 253 = 3542 kg |
| Implied density | 6.85 kg/kWh (hardcoded) | 253/37 = 6.84 kg/kWh |

**Status:** Very close for current fleet (6.85 vs 6.84 kg/kWh). Hardcoded in model metadata.
**Risk:** If future bus models have different battery pack densities, the model will be wrong.

### 2. Bus Body Weight: Fitted linear approximation

| What | Model computes | Actual specs |
|---|---|---|
| Body weight (18m bus) | 1014 * 18 + 2525 = 20,777 kg | empty_weight_kg = 18,000 kg |
| Body weight (12m bus) | 1014 * 12 + 2525 = 14,693 kg | empty_weight_kg = ~12,500 kg (TBD) |

**Status:** The `k1 * bus_length_m + k2` formula is a fitted approximation.
The value of 20,777 kg for an 18m bus includes an implicit average passenger load
(since the model was trained on data with real passengers). The actual empty weight
is 18,000 kg, so the difference (~2,777 kg) represents roughly 40 passengers at 70 kg
-- the average occupancy during training data collection.

**Risk:** This bakes an implicit average occupancy into the model. There is no way to
simulate empty buses vs. rush hour separately.

### 3. Passenger Occupancy: NOT modeled

The current greybox model has **no occupancy parameter**. The mass formula captures
only bus length and battery capacity. Any passenger weight effect is implicitly absorbed
into the fitted `k1` and `k2` parameters, representing whatever average occupancy was
present in the training data.

**Impact:** Cannot simulate:
- Empty bus deadhead runs (lower weight = lower consumption)
- Rush hour with full load (higher weight = higher consumption)
- Different route demographics (e.g., school routes vs. city center)

### 4. Battery Size Sensitivity: Uses model's density

**File:** `simulation/greybox_sensitivity.py`

```python
dE/d(bus_battery_kwh) = rho_batt * (alpha_roll * L + alpha_up * h_up + alpha_down * h_down)
```

Uses `battery_pack_density` from model metadata (6.85), not from actual bus specs.
Since the sensitivity is linear in `rho_batt`, this is easily correctable by scaling.

## Parameters Summary

| Parameter | Source | Hardcoded? | Can override at inference? |
|---|---|---|---|
| `battery_pack_density` | Model metadata | Yes (6.85) | No (read from saved model) |
| `k1` (weight per meter) | Fitted | Yes (1014.0) | No |
| `k2` (base weight) | Fitted | Yes (2525.0) | No |
| `alpha_roll` | Fitted | Yes | No |
| `alpha_aero` | Fitted | Yes | No |
| `alpha_up` | Fitted | Yes | No |
| `alpha_down` | Fitted | Yes | No |
| `bus_battery_kwh` | Feature input | No | Yes |
| `bus_length_m` | Feature input | No | Yes |
| `occupancy` | N/A | N/A | Not supported |
| `empty_weight_kg` | N/A | N/A | Not supported |
| `battery_pack_weight_kg` | N/A | N/A | Not supported |

## Recommendations for Future Improvement

### Short-term (can do without retraining)

1. **Override battery sensitivity with actual pack density**: When computing
   `mass_sensitivity_kwh_per_kwh_batt`, use `battery_pack_weight_kg / battery_pack_size_kwh`
   from the bus model specs instead of the model's `battery_pack_density`. This is a
   simple scaling: `sensitivity_actual = sensitivity_model * (actual_density / model_density)`.

2. **Occupancy correction factor**: Since the mass-dependent terms are linear in mass,
   we can compute a correction factor:
   ```
   m_model = battery_pack_density * batt_kwh + k1 * length + k2
   m_actual = empty_weight + n_packs * pack_weight + passengers * occupancy * 70
   correction = m_actual / m_model
   E_corrected = E_mass_terms * correction + E_aero_term
   ```
   This post-hoc correction would be approximate but better than no occupancy modeling.

### Medium-term (requires retraining)

3. **Add occupancy as a training feature**: Retrain the model with estimated occupancy
   data (from passenger counters or schedule-based estimates). This would make the model
   natively occupancy-aware.

4. **Replace parametric mass with actual mass as input feature**: Instead of computing
   mass from `bus_battery_kwh` and `bus_length_m` inside the model, pass `total_mass_kg`
   as an explicit input feature. This decouples the model from specific bus configurations
   and allows using actual spec-based weights at inference time.

5. **Per-bus-model `battery_pack_density`**: If different bus models use different
   battery chemistries, store and use per-model density rather than a single global value.

### Long-term

6. **Component-level weight modeling**: Separate the weight model into components
   (chassis, powertrain, battery, passengers, cargo) and allow each to vary independently.

## Phase 1 Implementation: Use Actual Mass (No Retraining Required)

The greybox model architecture allows us to override the mass without retraining:

1. `bus_battery_kwh` is **explicitly dropped** before the QRF residual model
   (`greybox_models.py` line 202). The QRF never sees mass or battery capacity --
   it only sees trip features (distance, speed, elevation, gradients, etc.).

2. The mass only affects `E_mech` (the physics-based greybox component), which is
   a pure physics formula where more accurate mass = more accurate physics.

3. The QRF was trained to predict `residual = actual - greybox_pred`. Since the
   QRF's features are independent of mass, its residual correction remains valid
   even if we change the greybox mass input.

### Changes needed in `simulation/greybox_models.py`

Modify `MechanicalGreyBox._predict_with_params()` to accept an optional
`override_mass` array. If provided, use it instead of computing mass internally:

```python
def _predict_with_params(self, X, theta, override_mass=None):
    alpha_roll, alpha_aero, alpha_up, alpha_down, k1, k2 = theta
    L, v, h_up, h_down, length, batt_kwh, driving_time, total_duration = self._extract_arrays(X)
    if override_mass is not None:
        m = np.asarray(override_mass, dtype=float)
    else:
        m = self.battery_pack_density * batt_kwh + k1 * length + k2
    # ... rest of E_mech calculation unchanged
```

Propagate through `predict()` and `CombinedGreyboxQRF.predict()`.

### Changes needed in prediction service

The prediction service computes actual mass from bus model specs:

```python
m_actual = (empty_weight_kg
            + num_battery_packs * battery_pack_weight_kg
            + max_passengers * (occupancy_percent / 100) * 70)
```

And passes it as `override_mass` to the model.

### Changes needed in sensitivity calculation

In `simulation/greybox_sensitivity.py`, use actual battery pack density instead
of the model's `battery_pack_density`:

```python
rho_actual = battery_pack_weight_kg / battery_pack_size_kwh  # e.g., 253/37 = 6.84
# instead of:
# rho_batt = greybox_params.get("battery_pack_density", 6.0)
```

This gives the correct `dE/d(bus_battery_kwh)` for the actual bus model.
