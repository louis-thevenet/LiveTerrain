#pragma once
#include "CoreMinimal.h"

class UWorld;
class ACesiumGeoreference;

namespace LocalTerrain
{
    /** Render geographically anchored trees using one shared native mesh. */
    void CreateTrees(UWorld* World, ACesiumGeoreference* Geo, const FString& Folder, FVector* PreviewLocation = nullptr);
}
