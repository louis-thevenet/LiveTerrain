#include "TerrainFlyPawn.h"
#include "Components/InputComponent.h"

ATerrainFlyPawn::ATerrainFlyPawn(const FObjectInitializer& ObjectInitializer)
    : Super(ObjectInitializer)
{
    bAddDefaultMovementBindings = false;
}

void ATerrainFlyPawn::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{
    Super::SetupPlayerInputComponent(PlayerInputComponent);
    check(PlayerInputComponent);
    PlayerInputComponent->BindAxis("TerrainMoveForward", this, &ADefaultPawn::MoveForward);
    PlayerInputComponent->BindAxis("TerrainMoveRight", this, &ADefaultPawn::MoveRight);
    PlayerInputComponent->BindAxis("TerrainMoveUp", this, &ADefaultPawn::MoveUp_World);
    PlayerInputComponent->BindAxis("TerrainLookYaw", this, &ADefaultPawn::AddControllerYawInput);
    PlayerInputComponent->BindAxis("TerrainLookPitch", this, &ADefaultPawn::AddControllerPitchInput);
}
