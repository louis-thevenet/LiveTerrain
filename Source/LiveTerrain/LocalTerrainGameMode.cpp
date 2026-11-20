#include "LocalTerrainGameMode.h"
#include "LocalTerrainEnvironment.h"
#include "LocalTerrainTrees.h"
#include "TerrainFlyPawn.h"
#include "TerrainHUD.h"
#include "Cesium3DTileset.h"
#include "CesiumGeoreference.h"
#include "CesiumTile.h"
#include <Cesium3DTilesSelection/Tileset.h>
#include "Camera/PlayerCameraManager.h"
#include "GameFramework/DefaultPawn.h"
#include "GameFramework/FloatingPawnMovement.h"
#include "GameFramework/PlayerController.h"
#include "GameFramework/WorldSettings.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "TimerManager.h"
#include "UnrealClient.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

DEFINE_LOG_CATEGORY_STATIC(LogLocalTerrain, Log, All);

bool ULocalTileRange::ShouldExclude_Implementation(const UCesiumTile* TileObject)
{
    const APlayerCameraManager* Camera = UGameplayStatics::GetPlayerCameraManager(this, 0);
    if (!Camera || !TileObject) return false;
    const FBoxSphereBounds& Bounds = TileObject->Bounds;
    return FVector::Dist(Camera->GetCameraLocation(), Bounds.Origin) > RangeCm + Bounds.SphereRadius;
}

ALocalTerrainGameMode::ALocalTerrainGameMode()
{
    DefaultPawnClass = ATerrainFlyPawn::StaticClass();
    HUDClass = ATerrainHUD::StaticClass();
}

void ALocalTerrainGameMode::StartPlay()
{
    GetWorld()->GetWorldSettings()->bEnableWorldBoundsChecks = false;
    FString Folder = TEXT("Prepared/zone");
    const TCHAR* Section = TEXT("LiveTerrain.LocalData");
    GConfig->GetString(Section, TEXT("Directory"), Folder, GGameIni);
    FParse::Value(FCommandLine::Get(), TEXT("GeoData="), Folder);
    Folder = FPaths::ConvertRelativePathToFull(FPaths::ProjectDir(), Folder);
    FString Json;
    TSharedPtr<FJsonObject> Zone;
    if (!FFileHelper::LoadFileToString(Json, *(Folder / TEXT("zone.json"))) ||
        !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Json), Zone) || !Zone.IsValid() ||
        !FPaths::FileExists(Folder / TEXT("tileset.json")))
    {
        UE_LOG(LogLocalTerrain, Error, TEXT("Missing/invalid prepared zone in %s. Run make prepare."), *Folder);
        Super::StartPlay();
        return;
    }
    double Lon, Lat, Height, Schema;
    if (!Zone->TryGetNumberField(TEXT("schema"), Schema) || Schema != 1 ||
        !Zone->TryGetNumberField(TEXT("longitude"), Lon) ||
        !Zone->TryGetNumberField(TEXT("latitude"), Lat) ||
        !Zone->TryGetNumberField(TEXT("height"), Height) ||
        !FMath::IsFinite(Lon) || !FMath::IsFinite(Lat) || !FMath::IsFinite(Height) ||
        FMath::Abs(Lon) > 180 || FMath::Abs(Lat) > 90)
    {
        UE_LOG(LogLocalTerrain, Error, TEXT("Invalid zone.json geographic metadata"));
        Super::StartPlay();
        return;
    }
    ACesiumGeoreference* Geo = ACesiumGeoreference::GetDefaultGeoreference(this);
    Geo->SetOriginLongitudeLatitudeHeight(FVector(Lon, Lat, Height));
    FVector TreePreviewLocation = FVector::ZeroVector;
    LocalTerrain::CreateTrees(GetWorld(), Geo, Folder, &TreePreviewLocation);
    ACesium3DTileset* Tiles = GetWorld()->SpawnActor<ACesium3DTileset>();
    Tiles->SetGeoreference(Geo);
    Tiles->SetTilesetSource(ETilesetSource::FromUrl);
    Tiles->MaximumSimultaneousTileLoads = 4;
    Tiles->MaximumCachedBytes = 1024LL * 1024 * 1024;
    Tiles->PreloadAncestors = false;
    Tiles->PreloadSiblings = false;
    Tiles->LogSelectionStats = true;
    int32 CacheMiB = 1024;
    GConfig->GetInt(Section, TEXT("CacheMiB"), CacheMiB, GGameIni);
    Tiles->MaximumCachedBytes = int64(FMath::Max(0, CacheMiB)) * 1024 * 1024;
    int32 Loads = 4;
    GConfig->GetInt(Section, TEXT("ConcurrentLoads"), Loads, GGameIni);
    Tiles->MaximumSimultaneousTileLoads = FMath::Clamp(Loads, 1, 32);
    GConfig->GetBool(Section, TEXT("LogTileStats"), Tiles->LogSelectionStats, GGameIni);
    ULocalTileRange* Range = NewObject<ULocalTileRange>(Tiles);
    double LoadDistance = 10000;
    GConfig->GetDouble(Section, TEXT("LoadDistanceMeters"), LoadDistance, GGameIni);
    Range->RangeCm = FMath::Clamp(LoadDistance, 50., 100000.) * 100.;
    Tiles->AddInstanceComponent(Range);
    Range->RegisterComponent();
    Range->Activate(true);
    FString Url = TEXT("file://") + (Folder / TEXT("tileset.json"));
    Url.ReplaceInline(TEXT("%"), TEXT("%25"));
    Url.ReplaceInline(TEXT(" "), TEXT("%20"));
    Url.ReplaceInline(TEXT("#"), TEXT("%23"));
    Url.ReplaceInline(TEXT("?"), TEXT("%3F"));
    Tiles->SetUrl(Url);

    LocalTerrain::CreateEnvironment(GetWorld());

    Super::StartPlay();
    if (APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0))
    {
        if (APawn* Pawn = PC->GetPawn())
        {
            Pawn->SetActorLocation(Geo->TransformLongitudeLatitudeHeightPositionToUnreal(FVector(Lon, Lat, Height + 150)));
            PC->SetControlRotation(FRotator(-15, 0, 0));
            if (FParse::Param(FCommandLine::Get(), TEXT("GeoTreePreview")) && !TreePreviewLocation.IsZero())
            {
                Pawn->SetActorLocation(TreePreviewLocation + FVector(-2500, 0, 700));
                PC->SetControlRotation(FVector(2500, 0, -100).Rotation());
            }
            if (UFloatingPawnMovement* Move = Pawn->FindComponentByClass<UFloatingPawnMovement>())
                Move->MaxSpeed = 15000.f;
        }
    }
    // Optional repeatable integration run in a separate -game process.
    if (FParse::Param(FCommandLine::Get(), TEXT("GeoSmokeTest")))
    {
        Tiles->MaximumCachedBytes = 0;
        CacheMiB = 0;
        if (APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0))
        {
            PC->SetIgnoreMoveInput(true);
            PC->SetIgnoreLookInput(true);
            if (APawn* Pawn = PC->GetPawn()) Pawn->DisableInput(PC);
        }
        GetWorldTimerManager().SetTimer(SmokeTimer, FTimerDelegate::CreateWeakLambda(this,
            [this, Tiles, Step = 0]() mutable
            {
                APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0);
                if (!PC || !PC->GetPawn()) return;
                ++Step;
                if (Step == 2 || Step == 4 || Step == 6 || Step == 8)
                {
                    const double East = Step == 2 ? 80000. : (Step == 4 ? -80000. : (Step == 6 ? 3000000. : 0.));
                    if (UFloatingPawnMovement* Move = PC->GetPawn()->FindComponentByClass<UFloatingPawnMovement>())
                        Move->StopMovementImmediately();
                    PC->GetPawn()->SetActorLocation(FVector(East, 0, 15000));
                    PC->SetControlRotation(FRotator(-15, Step == 2 ? 180 : 0, 0));
                    UE_LOG(LogLocalTerrain, Display, TEXT("Smoke move: east %.0f m"), East / 100.);
                }
                else if (Step <= 9)
                {
                    UE_LOG(LogLocalTerrain, Display, TEXT("Smoke capture %d: load progress %.1f%%, position %s"),
                        Step, Tiles->GetLoadProgress(), *PC->GetPawn()->GetActorLocation().ToString());
                    if (const auto* Native = Tiles->GetTileset())
                        UE_LOG(LogLocalTerrain, Display, TEXT("Smoke cache: %d loaded tiles, %lld bytes, budget %lld bytes"),
                            Native->getNumberOfTilesLoaded(), static_cast<long long>(Native->getTotalDataBytes()),
                            static_cast<long long>(Native->getOptions().maximumCachedBytes));
                    FScreenshotRequest::RequestScreenshot(FPaths::ProjectSavedDir() / FString::Printf(TEXT("Screenshots/GeoSmoke_%d.png"), Step), false, false);
                }
                else
                {
                    GetWorldTimerManager().ClearTimer(SmokeTimer);
                    UE_LOG(LogLocalTerrain, Display, TEXT("Smoke run complete"));
                    PC->ConsoleCommand(TEXT("quit"));
                }
            }), 15.f, true);
    }
    UE_LOG(LogLocalTerrain, Display, TEXT("Offline zone: %s; origin %.8f %.8f %.2f; range %.0f m, cache %d MiB"),
        *Url, Lon, Lat, Height, LoadDistance, CacheMiB);
}
