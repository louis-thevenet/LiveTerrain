#pragma once
#include "CoreMinimal.h"
#include "GameFramework/HUD.h"
#include "TerrainHUD.generated.h"

/** Small in-game FPS counter, including builds without console statistics. */
UCLASS()
class LIVETERRAIN_API ATerrainHUD : public AHUD
{
    GENERATED_BODY()
public:
    virtual void DrawHUD() override;
private:
    double SmoothedFrameSeconds = 0.;
};
