#include "LocalTerrainEnvironment.h"
#include "CoreMinimal.h"
#include "Engine/World.h"
#include "Engine/DirectionalLight.h"
#include "Engine/SkyLight.h"
#include "Engine/ExponentialHeightFog.h"
#include "Engine/PostProcessVolume.h"
#include "Components/DirectionalLightComponent.h"
#include "Components/SkyLightComponent.h"
#include "Components/SkyAtmosphereComponent.h"
#include "Components/VolumetricCloudComponent.h"
#include "Components/ExponentialHeightFogComponent.h"
#include "Materials/MaterialInterface.h"
#include "Misc/ConfigCacheIni.h"

DEFINE_LOG_CATEGORY_STATIC(LogTerrainEnvironment, Log, All);

void LocalTerrain::CreateEnvironment(UWorld* World)
{
    check(World);
    const TCHAR* Section = TEXT("LiveTerrain.Environment");
    double SunLux = 50000., SunPitch = -45., SunYaw = -30., FogDensity = .0015;
    bool CloudsEnabled = true;
    GConfig->GetDouble(Section, TEXT("SunIntensityLux"), SunLux, GGameIni);
    GConfig->GetDouble(Section, TEXT("SunPitch"), SunPitch, GGameIni);
    GConfig->GetDouble(Section, TEXT("SunYaw"), SunYaw, GGameIni);
    GConfig->GetDouble(Section, TEXT("FogDensity"), FogDensity, GGameIni);
    GConfig->GetBool(Section, TEXT("VolumetricClouds"), CloudsEnabled, GGameIni);

    World->SpawnActor<ASkyAtmosphere>();
    ADirectionalLight* Sun = World->SpawnActor<ADirectionalLight>(FVector::ZeroVector,
        FRotator(FMath::Clamp(SunPitch, -89., -1.), SunYaw, 0));
    UDirectionalLightComponent* SunLight = CastChecked<UDirectionalLightComponent>(Sun->GetLightComponent());
    SunLight->SetMobility(EComponentMobility::Movable);
    SunLight->SetIntensity(FMath::Clamp(SunLux, 1000., 120000.));
    SunLight->SetAtmosphereSunLight(true);
    SunLight->SetAtmosphereSunLightIndex(0);
    SunLight->bCastCloudShadows = CloudsEnabled;
    SunLight->CloudShadowStrength = .35f;
    SunLight->CloudShadowExtent = 8.f;
    SunLight->MarkRenderStateDirty();

    if (CloudsEnabled)
    {
        AVolumetricCloud* Clouds = World->SpawnActor<AVolumetricCloud>();
        UVolumetricCloudComponent* Volume = Clouds->FindComponentByClass<UVolumetricCloudComponent>();
        UMaterialInterface* Material = LoadObject<UMaterialInterface>(nullptr,
            TEXT("/Engine/EngineSky/VolumetricClouds/m_SimpleVolumetricCloud_Inst.m_SimpleVolumetricCloud_Inst"));
        if (Volume && Material)
        {
            Volume->SetMaterial(Material);
            Volume->SetLayerBottomAltitude(2.f);
            Volume->SetLayerHeight(2.f);
            Volume->SetViewSampleCountScale(.5f);
            Volume->SetShadowViewSampleCountScale(.5f);
        }
        else
        {
            UE_LOG(LogTerrainEnvironment, Error, TEXT("Missing native Unreal cloud component or material"));
        }
    }

    ASkyLight* Sky = World->SpawnActor<ASkyLight>();
    Sky->GetLightComponent()->SetMobility(EComponentMobility::Movable);
    Sky->GetLightComponent()->SetIntensity(1.f);
    Sky->GetLightComponent()->SetRealTimeCaptureEnabled(true);

    AExponentialHeightFog* Fog = World->SpawnActor<AExponentialHeightFog>();
    Fog->GetComponent()->SetFogDensity(FMath::Clamp(FogDensity, 0., .02));
    Fog->GetComponent()->SetFogHeightFalloff(.2f);
    Fog->GetComponent()->SetStartDistance(10000.f);
    Fog->GetComponent()->SetFogInscatteringColor(FLinearColor(.65f, .75f, .85f));
    Fog->GetComponent()->SetVolumetricFog(true);
    Fog->GetComponent()->SetVolumetricFogDistance(100000.f);

    APostProcessVolume* Exposure = World->SpawnActor<APostProcessVolume>();
    Exposure->bUnbound = true;
    // The project enables the extended exposure range: these limits are EV100.
    Exposure->Settings.bOverride_AutoExposureMinBrightness = true;
    Exposure->Settings.AutoExposureMinBrightness = 9.f;
    Exposure->Settings.bOverride_AutoExposureMaxBrightness = true;
    Exposure->Settings.AutoExposureMaxBrightness = 16.f;
    Exposure->Settings.bOverride_AutoExposureBias = true;
    Exposure->Settings.AutoExposureBias = 0.f;
    Exposure->Settings.bOverride_BloomIntensity = true;
    Exposure->Settings.BloomIntensity = .15f;

    UE_LOG(LogTerrainEnvironment, Display, TEXT("Native daylight: atmosphere, sun %.0f lux, skylight capture, clouds %s, volumetric fog %.4f"),
        SunLux, CloudsEnabled ? TEXT("on") : TEXT("off"), FogDensity);
}
