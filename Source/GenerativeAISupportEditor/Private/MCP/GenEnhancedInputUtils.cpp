// Copyright (c) 2025 Prajwal Shetty. All rights reserved.
// Licensed under the MIT License. See LICENSE file in the root directory of this
// source tree or http://opensource.org/licenses/MIT.
#include "MCP/GenEnhancedInputUtils.h"

#include "AssetToolsModule.h"
#include "Dom/JsonObject.h"
#include "Factories/Factory.h"
#include "IAssetTools.h"
#include "InputAction.h"
#include "InputCoreTypes.h"
#include "InputMappingContext.h"
#include "InputModifiers.h"
#include "InputTriggers.h"
#include "Misc/PackageName.h"
#include "Modules/ModuleManager.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

namespace
{
	FString SerializeJson(const TSharedRef<FJsonObject>& Object)
	{
		FString Out;
		TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Out);
		FJsonSerializer::Serialize(Object, Writer);
		return Out;
	}

	bool DecodeStringArray(const FString& Json, TArray<FString>& OutValues)
	{
		if (Json.IsEmpty()) return true;
		TSharedPtr<FJsonValue> Value;
		TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Json);
		if (!FJsonSerializer::Deserialize(Reader, Value) || !Value.IsValid()) return false;
		const TArray<TSharedPtr<FJsonValue>>* Arr;
		if (!Value->TryGetArray(Arr)) return false;
		for (const TSharedPtr<FJsonValue>& Entry : *Arr)
		{
			FString S;
			if (Entry.IsValid() && Entry->TryGetString(S))
			{
				OutValues.Add(S);
			}
		}
		return true;
	}

	EInputActionValueType ResolveValueType(const FString& Token)
	{
		if (Token.Equals(TEXT("Axis1D"), ESearchCase::IgnoreCase)) return EInputActionValueType::Axis1D;
		if (Token.Equals(TEXT("Axis2D"), ESearchCase::IgnoreCase)) return EInputActionValueType::Axis2D;
		if (Token.Equals(TEXT("Axis3D"), ESearchCase::IgnoreCase)) return EInputActionValueType::Axis3D;
		return EInputActionValueType::Boolean;
	}

	UClass* ResolveTriggerClass(const FString& Name)
	{
		if (Name == TEXT("Pressed")) return UInputTriggerPressed::StaticClass();
		if (Name == TEXT("Released")) return UInputTriggerReleased::StaticClass();
		if (Name == TEXT("Down")) return UInputTriggerDown::StaticClass();
		if (Name == TEXT("Hold")) return UInputTriggerHold::StaticClass();
		if (Name == TEXT("HoldAndRelease")) return UInputTriggerHoldAndRelease::StaticClass();
		if (Name == TEXT("Tap")) return UInputTriggerTap::StaticClass();
		if (Name == TEXT("Pulse")) return UInputTriggerPulse::StaticClass();
		if (Name == TEXT("ChordAction")) return UInputTriggerChordAction::StaticClass();
		return nullptr;
	}

	UClass* ResolveModifierClass(const FString& Name)
	{
		if (Name == TEXT("Negate")) return UInputModifierNegate::StaticClass();
		if (Name == TEXT("DeadZone")) return UInputModifierDeadZone::StaticClass();
		if (Name == TEXT("Scalar")) return UInputModifierScalar::StaticClass();
		if (Name == TEXT("Smooth")) return UInputModifierSmooth::StaticClass();
		if (Name == TEXT("SwizzleAxis")) return UInputModifierSwizzleAxis::StaticClass();
		if (Name == TEXT("FOVScaling")) return UInputModifierFOVScaling::StaticClass();
		return nullptr;
	}

	UPackage* CreateOrLoadPackage(const FString& PackageName)
	{
		UPackage* Package = CreatePackage(*PackageName);
		if (Package)
		{
			Package->FullyLoad();
		}
		return Package;
	}

	bool SavePackageSafe(UPackage* Package, UObject* Asset, const FString& FileName)
	{
		FSavePackageArgs Args;
		Args.TopLevelFlags = RF_Public | RF_Standalone;
		Args.SaveFlags = SAVE_NoError;
		return UPackage::SavePackage(Package, Asset, *FileName, Args);
	}
}

FString UGenEnhancedInputUtils::CreateInputAction(
	const FString& Name, const FString& SavePath, const FString& ValueType, const FString& /*Description*/)
{
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	const FString PackageName = SavePath / Name;
	UPackage* Package = CreateOrLoadPackage(PackageName);
	if (!Package)
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("Unable to create package"));
		return SerializeJson(Result);
	}
	UInputAction* Action = NewObject<UInputAction>(Package, *Name, RF_Public | RF_Standalone);
	Action->ValueType = ResolveValueType(ValueType);
	Action->MarkPackageDirty();

	const FString FileName = FPackageName::LongPackageNameToFilename(PackageName, FPackageName::GetAssetPackageExtension());
	const bool bSaved = SavePackageSafe(Package, Action, FileName);
	Result->SetBoolField(TEXT("success"), bSaved);
	Result->SetStringField(TEXT("asset_path"), Action->GetPathName());
	return SerializeJson(Result);
}

FString UGenEnhancedInputUtils::CreateInputMappingContext(const FString& Name, const FString& SavePath)
{
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	const FString PackageName = SavePath / Name;
	UPackage* Package = CreateOrLoadPackage(PackageName);
	if (!Package)
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("Unable to create package"));
		return SerializeJson(Result);
	}
	UInputMappingContext* Context = NewObject<UInputMappingContext>(Package, *Name, RF_Public | RF_Standalone);
	Context->MarkPackageDirty();
	const FString FileName = FPackageName::LongPackageNameToFilename(PackageName, FPackageName::GetAssetPackageExtension());
	const bool bSaved = SavePackageSafe(Package, Context, FileName);
	Result->SetBoolField(TEXT("success"), bSaved);
	Result->SetStringField(TEXT("asset_path"), Context->GetPathName());
	return SerializeJson(Result);
}

FString UGenEnhancedInputUtils::MapEnhancedInputAction(
	const FString& ContextPath, const FString& ActionPath, const FString& Key,
	const FString& TriggersJson, const FString& ModifiersJson)
{
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	UInputMappingContext* Context = LoadObject<UInputMappingContext>(nullptr, *ContextPath);
	UInputAction* Action = LoadObject<UInputAction>(nullptr, *ActionPath);
	if (!Context || !Action)
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("Could not load context or action"));
		return SerializeJson(Result);
	}

	FKey InputKey(*Key);
	if (!InputKey.IsValid())
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), FString::Printf(TEXT("Unknown key: %s"), *Key));
		return SerializeJson(Result);
	}

	FEnhancedActionKeyMapping& Mapping = Context->MapKey(Action, InputKey);

	TArray<FString> Triggers;
	TArray<FString> Modifiers;
	DecodeStringArray(TriggersJson, Triggers);
	DecodeStringArray(ModifiersJson, Modifiers);

	for (const FString& TriggerName : Triggers)
	{
		if (UClass* Cls = ResolveTriggerClass(TriggerName))
		{
			UInputTrigger* Trigger = NewObject<UInputTrigger>(Context, Cls);
			Mapping.Triggers.Add(Trigger);
		}
	}
	for (const FString& ModifierName : Modifiers)
	{
		if (UClass* Cls = ResolveModifierClass(ModifierName))
		{
			UInputModifier* Modifier = NewObject<UInputModifier>(Context, Cls);
			Mapping.Modifiers.Add(Modifier);
		}
	}

	Context->MarkPackageDirty();
	const FString FileName = FPackageName::LongPackageNameToFilename(Context->GetOutermost()->GetName(),
		FPackageName::GetAssetPackageExtension());
	const bool bSaved = SavePackageSafe(Context->GetOutermost(), Context, FileName);

	Result->SetBoolField(TEXT("success"), bSaved);
	Result->SetStringField(TEXT("context_path"), Context->GetPathName());
	Result->SetStringField(TEXT("action_path"), Action->GetPathName());
	Result->SetStringField(TEXT("key"), Key);
	return SerializeJson(Result);
}

FString UGenEnhancedInputUtils::ListInputMappings(const FString& ContextPath)
{
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	UInputMappingContext* Context = LoadObject<UInputMappingContext>(nullptr, *ContextPath);
	if (!Context)
	{
		Result->SetBoolField(TEXT("success"), false);
		Result->SetStringField(TEXT("error"), TEXT("Context not found"));
		return SerializeJson(Result);
	}

	TArray<TSharedPtr<FJsonValue>> Mappings;
	for (const FEnhancedActionKeyMapping& Mapping : Context->GetMappings())
	{
		TSharedRef<FJsonObject> Entry = MakeShared<FJsonObject>();
		Entry->SetStringField(TEXT("action_path"), Mapping.Action ? Mapping.Action->GetPathName() : TEXT(""));
		Entry->SetStringField(TEXT("key"), Mapping.Key.ToString());

		TArray<TSharedPtr<FJsonValue>> Triggers;
		for (const UInputTrigger* Trigger : Mapping.Triggers)
		{
			if (Trigger) Triggers.Add(MakeShared<FJsonValueString>(Trigger->GetClass()->GetName()));
		}
		Entry->SetArrayField(TEXT("triggers"), Triggers);

		TArray<TSharedPtr<FJsonValue>> Modifiers;
		for (const UInputModifier* Modifier : Mapping.Modifiers)
		{
			if (Modifier) Modifiers.Add(MakeShared<FJsonValueString>(Modifier->GetClass()->GetName()));
		}
		Entry->SetArrayField(TEXT("modifiers"), Modifiers);

		Mappings.Add(MakeShared<FJsonValueObject>(Entry));
	}

	Result->SetBoolField(TEXT("success"), true);
	Result->SetArrayField(TEXT("mappings"), Mappings);
	Result->SetStringField(TEXT("context_path"), Context->GetPathName());
	return SerializeJson(Result);
}
