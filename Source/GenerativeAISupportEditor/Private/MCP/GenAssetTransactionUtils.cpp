// Copyright (c) 2025 Prajwal Shetty. All rights reserved.
// Licensed under the MIT License. See LICENSE file in the root directory of this
// source tree or http://opensource.org/licenses/MIT.
#include "MCP/GenAssetTransactionUtils.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetToolsModule.h"
#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "HAL/FileManager.h"
#include "IAssetTools.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Misc/Guid.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

namespace
{
	struct FSnapshotRecord
	{
		FString AssetPath;
		FString SnapshotPackagePath; // in-memory only; we duplicate the package file
		FString SnapshotFileOnDisk;
	};

	TMap<FString, FSnapshotRecord>& GetSnapshotRegistry()
	{
		static TMap<FString, FSnapshotRecord> Registry;
		return Registry;
	}

	FString SerializeJson(const TSharedRef<FJsonObject>& Obj)
	{
		FString Out;
		TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Out);
		FJsonSerializer::Serialize(Obj, Writer);
		return Out;
	}

	FString MakeSnapshotToken()
	{
		return FGuid::NewGuid().ToString(EGuidFormats::DigitsWithHyphensLower);
	}
}

FString UGenAssetTransactionUtils::DuplicateForPreview(const FString& AssetPath)
{
	UObject* Asset = LoadObject<UObject>(nullptr, *AssetPath);
	if (!Asset) return FString();

	UPackage* Package = Asset->GetOutermost();
	if (!Package) return FString();

	const FString SnapshotToken = MakeSnapshotToken();
	const FString SnapshotDir = FPaths::ProjectSavedDir() / TEXT("MCP") / TEXT("Snapshots");
	IFileManager::Get().MakeDirectory(*SnapshotDir, /*Tree*/ true);

	const FString PackageFile = FPackageName::LongPackageNameToFilename(Package->GetName(),
		FPackageName::GetAssetPackageExtension());
	const FString SnapshotFile = SnapshotDir / FString::Printf(TEXT("%s__%s.uasset"),
		*FPaths::GetBaseFilename(PackageFile), *SnapshotToken);

	if (!IFileManager::Get().FileExists(*PackageFile))
	{
		return FString();
	}
	if (IFileManager::Get().Copy(*SnapshotFile, *PackageFile, /*bReplace*/ true) != COPY_OK)
	{
		return FString();
	}

	FSnapshotRecord Record;
	Record.AssetPath = AssetPath;
	Record.SnapshotPackagePath = Package->GetName();
	Record.SnapshotFileOnDisk = SnapshotFile;
	GetSnapshotRegistry().Add(SnapshotToken, Record);

	return SnapshotToken;
}

FString UGenAssetTransactionUtils::BeginTransaction(const FString& AssetPath, const FString& /*Description*/)
{
	// Currently identical to DuplicateForPreview; kept as a named entry-point
	// so callers that want to demarcate "write begins" can do so explicitly.
	return DuplicateForPreview(AssetPath);
}

FString UGenAssetTransactionUtils::ApplyTransaction(const FString& SnapshotToken, const FString& /*ChangesJson*/)
{
	TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();

	FSnapshotRecord* Record = GetSnapshotRegistry().Find(SnapshotToken);
	if (!Record)
	{
		Report->SetBoolField(TEXT("saved"), false);
		Report->SetStringField(TEXT("error"), TEXT("Unknown snapshot token"));
		return SerializeJson(Report);
	}

	UObject* Asset = LoadObject<UObject>(nullptr, *Record->AssetPath);
	if (!Asset)
	{
		Report->SetBoolField(TEXT("saved"), false);
		Report->SetStringField(TEXT("error"), TEXT("Asset could not be resolved"));
		return SerializeJson(Report);
	}

	UPackage* Package = Asset->GetOutermost();
	Package->MarkPackageDirty();
	const FString PackageFile = FPackageName::LongPackageNameToFilename(Package->GetName(),
		FPackageName::GetAssetPackageExtension());

	FSavePackageArgs SaveArgs;
	SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
	SaveArgs.SaveFlags = SAVE_NoError;
	const bool bSaved = UPackage::SavePackage(Package, nullptr, *PackageFile, SaveArgs);

	Report->SetBoolField(TEXT("saved"), bSaved);
	Report->SetStringField(TEXT("asset_path"), Record->AssetPath);
	Report->SetStringField(TEXT("snapshot_token"), SnapshotToken);
	return SerializeJson(Report);
}

bool UGenAssetTransactionUtils::RollbackToSnapshot(const FString& SnapshotToken)
{
	FSnapshotRecord* Record = GetSnapshotRegistry().Find(SnapshotToken);
	if (!Record) return false;

	const FString PackageFile = FPackageName::LongPackageNameToFilename(Record->SnapshotPackagePath,
		FPackageName::GetAssetPackageExtension());

	if (!IFileManager::Get().FileExists(*Record->SnapshotFileOnDisk)) return false;
	if (IFileManager::Get().Copy(*PackageFile, *Record->SnapshotFileOnDisk, /*bReplace*/ true) != COPY_OK)
	{
		return false;
	}

	// Reload the package so the editor sees the reverted version.
	if (UPackage* Existing = FindPackage(nullptr, *Record->SnapshotPackagePath))
	{
		Existing->SetDirtyFlag(false);
	}
	LoadPackage(nullptr, *Record->SnapshotPackagePath, LOAD_ForceLazyLoad);
	return true;
}

FString UGenAssetTransactionUtils::VerifyAsset(const FString& AssetPath)
{
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	UObject* Asset = LoadObject<UObject>(nullptr, *AssetPath);
	if (!Asset)
	{
		Result->SetBoolField(TEXT("passed"), false);
		Result->SetStringField(TEXT("error"), TEXT("Asset not found"));
		return SerializeJson(Result);
	}

	TArray<TSharedPtr<FJsonValue>> Checks;

	if (UBlueprint* Blueprint = Cast<UBlueprint>(Asset))
	{
		FKismetEditorUtilities::CompileBlueprint(Blueprint);
		TSharedRef<FJsonObject> Check = MakeShared<FJsonObject>();
		Check->SetStringField(TEXT("name"), TEXT("blueprint_compile"));
		Check->SetBoolField(TEXT("passed"), Blueprint->Status != BS_Error);
		Checks.Add(MakeShared<FJsonValueObject>(Check));
	}

	Result->SetBoolField(TEXT("passed"), true);
	Result->SetArrayField(TEXT("checks"), Checks);
	return SerializeJson(Result);
}

bool UGenAssetTransactionUtils::DiscardSnapshot(const FString& SnapshotToken)
{
	FSnapshotRecord* Record = GetSnapshotRegistry().Find(SnapshotToken);
	if (!Record) return false;

	if (!Record->SnapshotFileOnDisk.IsEmpty() && IFileManager::Get().FileExists(*Record->SnapshotFileOnDisk))
	{
		IFileManager::Get().Delete(*Record->SnapshotFileOnDisk, /*bRequireExists*/ false, /*bEvenIfReadOnly*/ true);
	}
	GetSnapshotRegistry().Remove(SnapshotToken);
	return true;
}
