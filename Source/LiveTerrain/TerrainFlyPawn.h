#pragma once
#include "CoreMinimal.h"
#include "GameFramework/DefaultPawn.h"
#include "TerrainFlyPawn.generated.h"

/** Free geographic camera with project-configured AZERTY movement. */
UCLASS()
class LIVETERRAIN_API ATerrainFlyPawn : public ADefaultPawn
{
    GENERATED_BODY()
public:
    ATerrainFlyPawn(const FObjectInitializer& ObjectInitializer);
    virtual void SetupPlayerInputComponent(UInputComponent* PlayerInputComponent) override;
};
