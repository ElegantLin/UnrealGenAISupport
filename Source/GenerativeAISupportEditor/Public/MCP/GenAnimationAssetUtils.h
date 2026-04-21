// Copyright (c) 2025 Prajwal Shetty. All rights reserved.
// Licensed under the MIT License. See LICENSE file in the root directory of this
// source tree or http://opensource.org/licenses/MIT.
#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "GenAnimationAssetUtils.generated.h"

/**
 * BlendSpace read / safe-write helpers (P3).
 * All write methods call ``ResampleData`` / ``ValidateSampleData`` /
 * ``PostEditChange`` before saving.
 */
UCLASS()
class GENERATIVEAISUPPORTEDITOR_API UGenAnimationAssetUtils : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	UFUNCTION(BlueprintCallable, Category = "Generative AI|Animation")
	static FString GetBlendSpaceInfo(const FString& BlendSpacePath);

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Animation")
	static FString SetBlendSpaceAxis(const FString& BlendSpacePath, int32 AxisIndex, const FString& AxisJson);

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Animation")
	static FString ReplaceBlendSpaceSamples(const FString& BlendSpacePath, const FString& SamplesJson);

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Animation")
	static FString SetBlendSpaceSampleAnimation(
		const FString& BlendSpacePath,
		int32 SampleIndex,
		const FString& AnimationPath);
};
