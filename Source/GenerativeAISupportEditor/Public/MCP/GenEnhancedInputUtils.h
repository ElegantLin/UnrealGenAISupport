// Copyright (c) 2025 Prajwal Shetty. All rights reserved.
// Licensed under the MIT License. See LICENSE file in the root directory of this
// source tree or http://opensource.org/licenses/MIT.
#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "GenEnhancedInputUtils.generated.h"

/**
 * Minimal Python-facing wrapper over Enhanced Input assets (P2).  All methods
 * return JSON strings so the handler layer can pass structured data back to
 * MCP callers.
 */
UCLASS()
class GENERATIVEAISUPPORTEDITOR_API UGenEnhancedInputUtils : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	UFUNCTION(BlueprintCallable, Category = "Generative AI|Input")
	static FString CreateInputAction(
		const FString& Name,
		const FString& SavePath,
		const FString& ValueType,
		const FString& Description);

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Input")
	static FString CreateInputMappingContext(const FString& Name, const FString& SavePath);

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Input")
	static FString MapEnhancedInputAction(
		const FString& ContextPath,
		const FString& ActionPath,
		const FString& Key,
		const FString& TriggersJson,
		const FString& ModifiersJson);

	UFUNCTION(BlueprintCallable, Category = "Generative AI|Input")
	static FString ListInputMappings(const FString& ContextPath);
};
