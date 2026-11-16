#pragma once
#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "TimerManager.h"
#include "CesiumTileExcluder.h"
#include "LocalTerrainGameMode.generated.h"

/** Distance gate for Cesium's own tile traversal, in Unreal centimetres. */
UCLASS()
class LIVETERRAIN_API ULocalTileRange : public UCesiumTileExcluder
{
    GENERATED_BODY()
public:
    double RangeCm = 1000000.0;
    virtual bool ShouldExclude_Implementation(const UCesiumTile* TileObject) override;
};

/** Creates a local-only geographic scene on an otherwise empty map. */
UCLASS()
class LIVETERRAIN_API ALocalTerrainGameMode : public AGameModeBase
{
    GENERATED_BODY()
public:
    ALocalTerrainGameMode();
    virtual void StartPlay() override;
private:
    FTimerHandle SmokeTimer;
};
