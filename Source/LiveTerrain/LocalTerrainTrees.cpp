#include "CoreMinimal.h"
#include "LocalTerrainTrees.h"
#include "CesiumGeoreference.h"
#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

DEFINE_LOG_CATEGORY_STATIC(LogTerrainTrees, Log, All);

void LocalTerrain::CreateTrees(UWorld* World, ACesiumGeoreference* Geo, const FString& Folder, FVector* PreviewLocation)
{
    const FString File = Folder / TEXT("trees.bin");
    if (!FPaths::FileExists(File)) return;
    FString MeshPath;
    double CullMeters = 3000.;
    GConfig->GetString(TEXT("LiveTerrain.Trees"), TEXT("Mesh"), MeshPath, GGameIni);
    GConfig->GetDouble(TEXT("LiveTerrain.Trees"), TEXT("CullDistanceMeters"), CullMeters, GGameIni);
    UStaticMesh* Mesh = LoadObject<UStaticMesh>(nullptr, *MeshPath);
    if (!Mesh)
    {
        UE_LOG(LogTerrainTrees, Error, TEXT("Tree mesh missing: %s"), *MeshPath);
        return;
    }
    TArray<uint8> Bytes;
    if (!FFileHelper::LoadFileToArray(Bytes, *File) || Bytes.Num() < 12 ||
        FMemory::Memcmp(Bytes.GetData(), "LTRE", 4) != 0)
    {
        UE_LOG(LogTerrainTrees, Error, TEXT("Invalid trees.bin header"));
        return;
    }
    uint32 Version, Count;
    FMemory::Memcpy(&Version, Bytes.GetData() + 4, 4);
    FMemory::Memcpy(&Count, Bytes.GetData() + 8, 4);
    if (Version != 1 || Count > 2000000 || int64(Bytes.Num()) != 12 + int64(Count) * 32)
    {
        UE_LOG(LogTerrainTrees, Error, TEXT("Invalid trees.bin version/count/length"));
        return;
    }
    const FBoxSphereBounds Bounds = Mesh->GetBounds();
    const double Width = 2. * FMath::Max(Bounds.BoxExtent.X, Bounds.BoxExtent.Y);
    const double Height = 2. * Bounds.BoxExtent.Z;
    if (Width <= 0 || Height <= 0) return;
    TArray<FTransform> Instances;
    Instances.Reserve(Count);
    FRandomStream Random(483376);
    double Nearest = TNumericLimits<double>::Max();
    for (uint32 I = 0; I < Count; ++I)
    {
        const uint8* Record = Bytes.GetData() + 12 + I * 32;
        double XYZ[3];
        float H, Diameter;
        FMemory::Memcpy(XYZ, Record, 24);
        FMemory::Memcpy(&H, Record + 24, 4);
        FMemory::Memcpy(&Diameter, Record + 28, 4);
        if (!FMath::IsFinite(XYZ[0]) || !FMath::IsFinite(XYZ[1]) || !FMath::IsFinite(XYZ[2]) ||
            !FMath::IsFinite(H) || !FMath::IsFinite(Diameter) || H <= 0 || H > 80 || Diameter <= 0 || Diameter > 50)
        {
            UE_LOG(LogTerrainTrees, Error, TEXT("Invalid tree record %u"), I);
            return;
        }
        const FVector Location = Geo->TransformEarthCenteredEarthFixedPositionToUnreal(FVector(XYZ[0], XYZ[1], XYZ[2]));
        if (PreviewLocation && Location.SizeSquared2D() < Nearest)
        {
            Nearest = Location.SizeSquared2D();
            *PreviewLocation = Location;
        }
        const FQuat Rotation = Geo->TransformEastSouthUpRotatorToUnreal(FRotator(0, Random.FRandRange(-180.f, 180.f), 0), Location).Quaternion();
        const FVector Scale(Diameter * 100. / Width, Diameter * 100. / Width, H * 100. / Height);
        const FVector PivotOffset = Rotation.RotateVector(FVector(0, 0, (Bounds.Origin.Z - Bounds.BoxExtent.Z) * Scale.Z));
        Instances.Emplace(Rotation, Location - PivotOffset, Scale);
    }
    AActor* Actor = World->SpawnActor<AActor>();
#if WITH_EDITOR
    Actor->SetActorLabel(TEXT("Geographic tree meshes"));
#endif
    UHierarchicalInstancedStaticMeshComponent* Trees = NewObject<UHierarchicalInstancedStaticMeshComponent>(Actor);
    Actor->SetRootComponent(Trees);
    Actor->AddInstanceComponent(Trees);
    Trees->SetMobility(EComponentMobility::Static);
    Trees->SetStaticMesh(Mesh);
    Trees->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Trees->SetCanEverAffectNavigation(false);
    Trees->SetCullDistances(FMath::Clamp(CullMeters, 100., 10000.) * 90., FMath::Clamp(CullMeters, 100., 10000.) * 100.);
    Trees->bAutoRebuildTreeOnInstanceChanges = false;
    Trees->RegisterComponent();
    Trees->AddInstances(Instances, false, true, false);
    Trees->BuildTreeIfOutdated(true, true);
    UE_LOG(LogTerrainTrees, Display, TEXT("Native tree mesh %s: %u geographically anchored instances, cull %.0f m"), *MeshPath, Count, CullMeters);
}
