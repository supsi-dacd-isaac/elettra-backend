FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
COPY --from=vecto / /vecto/
WORKDIR /src
COPY VectoOracle.csproj Program.cs ./
RUN dotnet publish VectoOracle.csproj -c Release -o /oracle --no-self-contained

FROM mcr.microsoft.com/dotnet/runtime:8.0
WORKDIR /oracle
COPY --from=build /oracle ./
ENTRYPOINT ["dotnet", "/oracle/VectoOracle.dll"]
