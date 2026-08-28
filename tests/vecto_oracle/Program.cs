using System.Reflection;
using System.Text.Json;
using System.Text.Json.Serialization;
using TUGraz.VectoCommon.BusAuxiliaries;
using TUGraz.VectoCommon.Utils;
using TUGraz.VectoCore.Models.BusAuxiliaries.DownstreamModules.Impl.HVAC;
using TUGraz.VectoCore.Models.Declaration;

record OracleCase(
    string Name,
    int EnvironmentalId,
    double TemperatureCelsius,
    double SolarIrradianceWm2,
    Dictionary<string, double> HeatPumpCop,
    double ElectricHeaterEfficiency,
    double FuelHeaterEfficiency,
    double NumberOfPassengers,
    string FloorType,
    double SurfaceAreaM2,
    double WindowSurfaceM2,
    double VolumeM3,
    double UValueWPerKSquareM,
    string HvacConfiguration,
    string DriverHeatPump,
    string PassengerHeatPump,
    string ElectricHeater,
    double DriverCompartmentLengthM,
    double PassengerCompartmentLengthM,
    double MaxCoolingPowerDriverW,
    double MaxCoolingPowerPassengerW,
    double MaxHeatingPowerDriverW,
    double MaxHeatingPowerPassengerW,
    double FuelHeaterCapacityW,
    double VentilationRatePerHour,
    double VentilationRateHeatingPerHour,
    double SpecificVentilationPowerWhPerM3,
    bool VentilationOnDuringHeating,
    bool VentilationDuringCooling,
    bool VentilationWhenInactive,
    double EngineWasteHeatW = 0.0,
    double HeatingVariation = 0.0,
    double HeatingVentilationVariation = 0.0,
    double InactiveVentilationVariation = 0.0,
    double CoolingVentilationVariation = 0.0,
    double CoolingVariation = 0.0
);

record OracleResult(
    string Name,
    double ElectricalCoolingAndVentilationW,
    double MechanicalCoolingW,
    double RequiredHeatingPowerW,
    double ElectricalHeatPumpW,
    double MechanicalHeatPumpW,
    double ElectricHeaterW,
    double FuelHeaterW
);

static class Program
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        WriteIndented = true,
        NumberHandling = JsonNumberHandling.AllowNamedFloatingPointLiterals,
    };

    public static int Main(string[] args)
    {
        if (args.Length != 1)
        {
            Console.Error.WriteLine("Usage: VectoOracle CASES.json");
            return 2;
        }

        var cases = JsonSerializer.Deserialize<List<OracleCase>>(
            File.ReadAllText(args[0]), JsonOptions
        ) ?? throw new InvalidDataException("The oracle input must be a JSON array.");

        Console.WriteLine(JsonSerializer.Serialize(cases.Select(Run).ToList(), JsonOptions));
        return 0;
    }

    private static OracleResult Run(OracleCase item)
    {
        var driverHeatPump = ParseHeatPump(item.DriverHeatPump);
        var passengerHeatPump = ParseHeatPump(item.PassengerHeatPump);
        var electricHeater = ParseHeater(item.ElectricHeater);

        var cop = item.HeatPumpCop.ToDictionary(
            entry => ParseHeatPump(entry.Key), entry => entry.Value
        );

        var heaterEfficiency = new Dictionary<HeaterType, double>();
        if (electricHeater != HeaterType.None && !double.IsNaN(item.ElectricHeaterEfficiency))
        {
            foreach (var value in Enum.GetValues<HeaterType>())
                if (value != HeaterType.None && electricHeater.HasFlag(value))
                    heaterEfficiency[value] = item.ElectricHeaterEfficiency;
        }
        if (!double.IsNaN(item.FuelHeaterEfficiency))
            heaterEfficiency[HeaterType.FuelHeater] = item.FuelHeaterEfficiency;

        var environment = new EnvironmentalConditionMapEntry(
            item.EnvironmentalId,
            item.TemperatureCelsius.DegCelsiusToKelvin(),
            item.SolarIrradianceWm2.SI<WattPerSquareMeter>(),
            1.0,
            cop,
            heaterEfficiency
        );

        var inputs = new SSMInputs("elettra-vecto-oracle")
        {
            DefaultConditions = environment,
            EnvironmentalConditionsMap = null,
            HeatingBoundaryTemperature = 18.0.DegCelsiusToKelvin(),
            CoolingBoundaryTemperature = 23.0.DegCelsiusToKelvin(),
            GFactor = 0.95,
            MaxPossibleBenefitFromTechnologyList = 0.5,
            SpecificVentilationPower = (item.SpecificVentilationPowerWhPerM3 * 3600.0)
                .SI<JoulePerCubicMeter>(),
            AuxHeaterEfficiency = 0.84,
            FuelEnergyToHeatToCoolant = 0.0,
            CoolantHeatTransferredToAirCabinHeater = 0.0,
            ElectricWasteHeatToCoolant = 0.0,
            VentilationOnDuringHeating = item.VentilationOnDuringHeating,
            VentilationDuringAC = item.VentilationDuringCooling,
            VentilationWhenBothHeatingAndACInactive = item.VentilationWhenInactive,
            FuelFiredHeaterPower = item.FuelHeaterCapacityW.SI<Watt>(),
            Technologies = NewTechnologyBenefits(item),
            HeatingDistributions = DeclarationData.BusAuxiliaries.HeatingDistributionCases,
            HeatPumpTypeDriverCompartment = driverHeatPump,
            HeatPumpTypePassengerCompartment = passengerHeatPump,
            HVACSystemConfiguration = Enum.Parse<BusHVACSystemConfiguration>(item.HvacConfiguration),
            ElectricHeater = electricHeater,
            HVACMaxCoolingPowerDriver = item.MaxCoolingPowerDriverW.SI<Watt>(),
            HVACMaxCoolingPowerPassenger = item.MaxCoolingPowerPassengerW.SI<Watt>(),
            MaxHeatingPowerDriver = item.MaxHeatingPowerDriverW.SI<Watt>(),
            MaxHeatingPowerPassenger = item.MaxHeatingPowerPassengerW.SI<Watt>(),
            DriverCompartmentLength = item.DriverCompartmentLengthM.SI<Meter>(),
            PassengerCompartmentLength = item.PassengerCompartmentLengthM.SI<Meter>(),
            VentilationRate = (item.VentilationRatePerHour / 3600.0).SI<PerSecond>(),
        };

        SetInternal(inputs, nameof(SSMInputs.NumberOfPassengers), item.NumberOfPassengers);
        SetInternal(inputs, nameof(SSMInputs.BusFloorType), Enum.Parse<FloorType>(item.FloorType));
        SetInternal(inputs, nameof(SSMInputs.BusSurfaceArea), item.SurfaceAreaM2.SI<SquareMeter>());
        SetInternal(inputs, nameof(SSMInputs.BusWindowSurface), item.WindowSurfaceM2.SI<SquareMeter>());
        SetInternal(inputs, nameof(SSMInputs.BusVolumeVentilation), item.VolumeM3.SI<CubicMeter>());
        SetInternal(inputs, nameof(SSMInputs.UValue), item.UValueWPerKSquareM.SI<WattPerKelvinSquareMeter>());
        SetInternal(inputs, nameof(SSMInputs.VentilationRateHeating),
            (item.VentilationRateHeatingPerHour / 3600.0).SI<PerSecond>());

        var ssm = new SSMTOOL(inputs);
        var heater = ssm.AverageHeaterPower(item.EngineWasteHeatW.SI<Watt>());

        return new OracleResult(
            item.Name,
            ssm.ElectricalWAdjusted.Value(),
            ssm.MechanicalWBaseAdjusted.Value(),
            heater.RequiredHeatingPower.Value(),
            heater.HeatPumpPowerEl.Value(),
            heater.HeatPumpPowerMech.Value(),
            heater.ElectricHeaterPowerEl.Value(),
            heater.AuxHeaterPower.Value()
        );
    }

    private static TechnologyBenefits NewTechnologyBenefits(OracleCase item)
    {
        var result = new TechnologyBenefits();
        SetInternal(result, nameof(TechnologyBenefits.HValueVariation), item.HeatingVariation);
        SetInternal(result, nameof(TechnologyBenefits.VHValueVariation), item.HeatingVentilationVariation);
        SetInternal(result, nameof(TechnologyBenefits.VVValueVariation), item.InactiveVentilationVariation);
        SetInternal(result, nameof(TechnologyBenefits.VCValueVariation), item.CoolingVentilationVariation);
        SetInternal(result, nameof(TechnologyBenefits.CValueVariation), item.CoolingVariation);
        return result;
    }

    private static void SetInternal(object target, string propertyName, object value)
    {
        var property = target.GetType().GetProperty(
            propertyName, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic
        ) ?? throw new MissingMemberException(target.GetType().FullName, propertyName);
        var setter = property.GetSetMethod(nonPublic: true)
            ?? throw new MissingMethodException($"No setter for {target.GetType().FullName}.{propertyName}");
        setter.Invoke(target, new[] { value });
    }

    private static HeatPumpType ParseHeatPump(string value) => value switch
    {
        "none" => HeatPumpType.none,
        "R744" => HeatPumpType.R_744,
        "2stage" => HeatPumpType.non_R_744_2_stage,
        "3stage" => HeatPumpType.non_R_744_3_stage,
        "4stage" => HeatPumpType.non_R_744_4_stage,
        "continuous" => HeatPumpType.non_R_744_continuous,
        _ => throw new ArgumentOutOfRangeException(nameof(value), value, "Unknown heat-pump type"),
    };

    private static HeaterType ParseHeater(string value) => value switch
    {
        "none" => HeaterType.None,
        "water" => HeaterType.WaterElectricHeater,
        "air" => HeaterType.AirElectricHeater,
        "other" => HeaterType.OtherElectricHeating,
        _ => throw new ArgumentOutOfRangeException(nameof(value), value, "Unknown electric-heater type"),
    };
}
